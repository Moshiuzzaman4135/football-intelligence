from base64 import b64encode
from io import BytesIO
from pathlib import Path

import pytest

from football_intelligence.object_store import (
    FilesystemObjectStore,
    MultipartPresignUnsupported,
    S3ObjectStore,
)


def test_filesystem_adapter_streams_parts_and_rejects_path_traversal(tmp_path: Path):
    store = FilesystemObjectStore(tmp_path)
    upload_id = store.create_multipart("uploads/opaque/source.mp4", "video/mp4")

    first = store.upload_part(
        upload_id, "uploads/opaque/source.mp4", 1, BytesIO(b"first")
    )
    second = store.upload_part(
        upload_id, "uploads/opaque/source.mp4", 2, BytesIO(b"second")
    )
    completed = store.complete_multipart(
        upload_id, "uploads/opaque/source.mp4", [first, second]
    )

    assert completed.size_bytes == 11
    assert b"".join(store.iter_object(completed.object_key, chunk_size=3)) == b"firstsecond"
    assert store.object_exists(completed.object_key)
    with pytest.raises(ValueError, match="opaque"):
        store.create_multipart("../escape.mp4", "video/mp4")


def test_filesystem_adapter_refuses_browser_presigns(tmp_path: Path):
    store = FilesystemObjectStore(tmp_path)
    upload_id = store.create_multipart("uploads/opaque/source.mp4", "video/mp4")

    with pytest.raises(MultipartPresignUnsupported, match="test-only"):
        store.presign_part(
            upload_id,
            "uploads/opaque/source.mp4",
            1,
            60,
            expected_size_bytes=4,
            checksum_sha256="a" * 64,
        )


def test_interrupted_filesystem_part_is_not_resumable(tmp_path: Path):
    store = FilesystemObjectStore(tmp_path)
    key = "uploads/opaque/source.mp4"
    upload_id = store.create_multipart(key, "video/mp4")

    class InterruptedBody:
        reads = 0

        def read(self, _size):
            self.reads += 1
            if self.reads == 1:
                return b"partial"
            raise OSError("client disconnected")

    with pytest.raises(OSError, match="disconnected"):
        store.upload_part(upload_id, key, 1, InterruptedBody())

    assert store.list_parts(upload_id, key) == []


def test_failed_filesystem_assembly_never_publishes_partial_object(tmp_path: Path):
    store = FilesystemObjectStore(tmp_path)
    key = "uploads/opaque/source.mp4"
    upload_id = store.create_multipart(key, "video/mp4")
    first = store.upload_part(upload_id, key, 1, BytesIO(b"first"))
    second = store.upload_part(upload_id, key, 2, BytesIO(b"second"))
    (store.parts_root / upload_id / "2.part").unlink()

    with pytest.raises(FileNotFoundError):
        store.complete_multipart(upload_id, key, [first, second])

    assert not store.object_exists(key)


class StreamingBody:
    def __init__(self, chunks):
        self.chunks = chunks

    def iter_chunks(self, chunk_size):
        assert chunk_size == 1024
        yield from self.chunks


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def create_multipart_upload(self, **kwargs):
        self.calls.append(("create", kwargs))
        return {"UploadId": "s3-upload-1"}

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.calls.append(("presign", operation, Params, ExpiresIn))
        return "https://minio.test/presigned"

    def list_parts(self, **kwargs):
        self.calls.append(("list", kwargs))
        return {
            "Parts": [
                {
                    "PartNumber": 1,
                    "Size": 5,
                    "ETag": '"etag-1"',
                    "ChecksumSHA256": b64encode(bytes.fromhex("a" * 64)).decode(),
                },
                {"PartNumber": 2, "Size": 6, "ETag": '"etag-2"'},
            ],
            "IsTruncated": False,
        }

    def complete_multipart_upload(self, **kwargs):
        self.calls.append(("complete", kwargs))
        return {"ETag": '"object-etag"'}

    def head_object(self, **kwargs):
        self.calls.append(("head", kwargs))
        return {"ContentLength": 11, "ETag": '"object-etag"'}

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        return {"Body": StreamingBody([b"first", b"second"])}

    def abort_multipart_upload(self, **kwargs):
        self.calls.append(("abort", kwargs))

    def delete_object(self, **kwargs):
        self.calls.append(("delete", kwargs))


class S3Error(Exception):
    def __init__(self, code, status):
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


def test_ensure_bucket_only_creates_for_not_found_and_uses_region():
    class MissingBucketClient(FakeS3Client):
        def head_bucket(self, **kwargs):
            self.calls.append(("head_bucket", kwargs))
            raise S3Error("NoSuchBucket", 404)

        def create_bucket(self, **kwargs):
            self.calls.append(("create_bucket", kwargs))

    client = MissingBucketClient()
    store = S3ObjectStore(client=client, bucket="football-media", region="eu-west-1")

    store.ensure_bucket()

    create = next(call for call in client.calls if call[0] == "create_bucket")
    assert create[1]["CreateBucketConfiguration"] == {
        "LocationConstraint": "eu-west-1"
    }


def test_ensure_bucket_reraises_network_or_auth_errors_without_create():
    failure = TimeoutError("DNS timeout")

    class FailingClient(FakeS3Client):
        def head_bucket(self, **kwargs):
            self.calls.append(("head_bucket", kwargs))
            raise failure

        def create_bucket(self, **kwargs):
            self.calls.append(("create_bucket", kwargs))

    client = FailingClient()
    store = S3ObjectStore(client=client, bucket="football-media")

    with pytest.raises(TimeoutError, match="DNS timeout") as captured:
        store.ensure_bucket()

    assert captured.value is failure
    assert not any(call[0] == "create_bucket" for call in client.calls)


def test_s3_adapter_maps_multipart_calls_and_streams_downloads():
    client = FakeS3Client()
    presign_client = FakeS3Client()
    store = S3ObjectStore(
        client=client, presign_client=presign_client, bucket="football-media"
    )
    key = "uploads/opaque/source.mp4"

    upload_id = store.create_multipart(key, "video/mp4")
    url = store.presign_part(
        upload_id,
        key,
        1,
        600,
        expected_size_bytes=5,
        checksum_sha256="a" * 64,
    )
    parts = store.list_parts(upload_id, key)
    completed = store.complete_multipart(upload_id, key, parts)

    assert upload_id == "s3-upload-1"
    assert url == "https://minio.test/presigned"
    assert not any(call[0] == "presign" for call in client.calls)
    assert any(call[0] == "presign" for call in presign_client.calls)
    presign_call = next(call for call in presign_client.calls if call[0] == "presign")
    assert presign_call[2]["ContentLength"] == 5
    assert presign_call[2]["ChecksumSHA256"] == "qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqo="
    assert [part.etag for part in parts] == ['"etag-1"', '"etag-2"']
    assert parts[0].checksum_sha256 == "a" * 64
    assert completed.uri == f"s3://football-media/{key}"
    assert b"".join(store.iter_object(key, chunk_size=1024)) == b"firstsecond"
    complete_call = next(call for call in client.calls if call[0] == "complete")
    assert complete_call[1]["MultipartUpload"]["Parts"] == [
        {"PartNumber": 1, "ETag": '"etag-1"'},
        {"PartNumber": 2, "ETag": '"etag-2"'},
    ]
