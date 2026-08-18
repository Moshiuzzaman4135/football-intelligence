from io import BytesIO
from pathlib import Path

import pytest

from football_intelligence.object_store import FilesystemObjectStore, S3ObjectStore


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
                {"PartNumber": 1, "Size": 5, "ETag": '"etag-1"'},
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


def test_s3_adapter_maps_multipart_calls_and_streams_downloads():
    client = FakeS3Client()
    presign_client = FakeS3Client()
    store = S3ObjectStore(
        client=client, presign_client=presign_client, bucket="football-media"
    )
    key = "uploads/opaque/source.mp4"

    upload_id = store.create_multipart(key, "video/mp4")
    url = store.presign_part(upload_id, key, 1, 600)
    parts = store.list_parts(upload_id, key)
    completed = store.complete_multipart(upload_id, key, parts)

    assert upload_id == "s3-upload-1"
    assert url == "https://minio.test/presigned"
    assert not any(call[0] == "presign" for call in client.calls)
    assert any(call[0] == "presign" for call in presign_client.calls)
    assert [part.etag for part in parts] == ['"etag-1"', '"etag-2"']
    assert completed.uri == f"s3://football-media/{key}"
    assert b"".join(store.iter_object(key, chunk_size=1024)) == b"firstsecond"
    complete_call = next(call for call in client.calls if call[0] == "complete")
    assert complete_call[1]["MultipartUpload"]["Parts"] == [
        {"PartNumber": 1, "ETag": '"etag-1"'},
        {"PartNumber": 2, "ETag": '"etag-2"'},
    ]
