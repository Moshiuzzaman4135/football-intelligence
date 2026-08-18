"""Object storage boundaries and local multipart adapters."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol, runtime_checkable
from uuid import uuid4


@dataclass(frozen=True)
class UploadedPart:
    part_number: int
    size_bytes: int
    etag: str
    checksum_sha256: str | None = None


@dataclass(frozen=True)
class StoredObject:
    object_key: str
    size_bytes: int
    etag: str
    uri: str


@runtime_checkable
class ObjectStore(Protocol):
    """Minimal multipart interface shared by local adapters and S3."""

    def create_multipart(self, object_key: str, content_type: str) -> str: ...

    def presign_part(
        self,
        storage_upload_id: str,
        object_key: str,
        part_number: int,
        expires_seconds: int,
    ) -> str: ...

    def list_parts(self, storage_upload_id: str, object_key: str) -> list[UploadedPart]: ...

    def complete_multipart(
        self,
        storage_upload_id: str,
        object_key: str,
        parts: list[UploadedPart],
    ) -> StoredObject: ...

    def abort_multipart(self, storage_upload_id: str, object_key: str) -> None: ...

    def delete_object(self, object_key: str) -> None: ...

    def object_exists(self, object_key: str) -> bool: ...

    def iter_object(self, object_key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]: ...

    def object_uri(self, object_key: str) -> str: ...


@dataclass
class _MemoryUpload:
    object_key: str
    parts: dict[int, tuple[UploadedPart, bytes]]


class InMemoryObjectStore:
    """Small deterministic adapter for unit tests."""

    def __init__(self) -> None:
        self._uploads: dict[str, _MemoryUpload] = {}
        self._objects: dict[str, bytes] = {}

    def create_multipart(self, object_key: str, content_type: str) -> str:
        del content_type
        _validate_object_key(object_key)
        upload_id = str(uuid4())
        self._uploads[upload_id] = _MemoryUpload(object_key=object_key, parts={})
        return upload_id

    def presign_part(
        self,
        storage_upload_id: str,
        object_key: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        self._get_upload(storage_upload_id, object_key)
        return (
            f"memory://uploads/{storage_upload_id}/parts/{part_number}"
            f"?expires={expires_seconds}"
        )

    def upload_part(
        self,
        storage_upload_id: str,
        object_key: str,
        part_number: int,
        body: bytes,
    ) -> UploadedPart:
        upload = self._get_upload(storage_upload_id, object_key)
        part = UploadedPart(
            part_number=part_number,
            size_bytes=len(body),
            etag=hashlib.md5(body, usedforsecurity=False).hexdigest(),
            checksum_sha256=hashlib.sha256(body).hexdigest(),
        )
        upload.parts[part_number] = (part, body)
        return part

    def list_parts(self, storage_upload_id: str, object_key: str) -> list[UploadedPart]:
        upload = self._uploads.get(storage_upload_id)
        if upload is None:
            return []
        if upload.object_key != object_key:
            raise ValueError("multipart upload does not belong to object key")
        return [upload.parts[number][0] for number in sorted(upload.parts)]

    def complete_multipart(
        self,
        storage_upload_id: str,
        object_key: str,
        parts: list[UploadedPart],
    ) -> StoredObject:
        upload = self._get_upload(storage_upload_id, object_key)
        body = b"".join(upload.parts[part.part_number][1] for part in parts)
        self._objects[object_key] = body
        del self._uploads[storage_upload_id]
        return StoredObject(
            object_key=object_key,
            size_bytes=len(body),
            etag=hashlib.md5(body, usedforsecurity=False).hexdigest(),
            uri=self.object_uri(object_key),
        )

    def abort_multipart(self, storage_upload_id: str, object_key: str) -> None:
        upload = self._uploads.get(storage_upload_id)
        if upload is not None and upload.object_key != object_key:
            raise ValueError("multipart upload does not belong to object key")
        self._uploads.pop(storage_upload_id, None)

    def delete_object(self, object_key: str) -> None:
        self._objects.pop(object_key, None)

    def object_exists(self, object_key: str) -> bool:
        return object_key in self._objects

    def iter_object(self, object_key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        body = self._objects[object_key]
        for offset in range(0, len(body), chunk_size):
            yield body[offset : offset + chunk_size]

    def object_uri(self, object_key: str) -> str:
        return f"memory://objects/{object_key}"

    def _get_upload(self, storage_upload_id: str, object_key: str) -> _MemoryUpload:
        try:
            upload = self._uploads[storage_upload_id]
        except KeyError as exc:
            raise KeyError("multipart upload not found") from exc
        if upload.object_key != object_key:
            raise ValueError("multipart upload does not belong to object key")
        return upload


class FilesystemObjectStore:
    """Streaming multipart adapter for local development and tests."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.parts_root = self.root / ".multipart"
        self.objects_root = self.root / "objects"
        self.parts_root.mkdir(parents=True, exist_ok=True)
        self.objects_root.mkdir(parents=True, exist_ok=True)

    def create_multipart(self, object_key: str, content_type: str) -> str:
        del content_type
        _validate_object_key(object_key)
        upload_id = str(uuid4())
        upload_path = self.parts_root / upload_id
        upload_path.mkdir()
        (upload_path / "object-key").write_text(object_key, encoding="utf-8")
        return upload_id

    def presign_part(
        self,
        storage_upload_id: str,
        object_key: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        self._upload_path(storage_upload_id, object_key)
        return (
            f"file://{self.parts_root / storage_upload_id / f'{part_number}.part'}"
            f"?expires={expires_seconds}"
        )

    def upload_part(
        self,
        storage_upload_id: str,
        object_key: str,
        part_number: int,
        body: BinaryIO,
    ) -> UploadedPart:
        upload_path = self._upload_path(storage_upload_id, object_key)
        part_path = upload_path / f"{part_number}.part"
        checksum = hashlib.sha256()
        etag = hashlib.md5(usedforsecurity=False)
        size = 0
        with part_path.open("wb") as target:
            while chunk := body.read(1024 * 1024):
                target.write(chunk)
                checksum.update(chunk)
                etag.update(chunk)
                size += len(chunk)
        return UploadedPart(part_number, size, etag.hexdigest(), checksum.hexdigest())

    def list_parts(self, storage_upload_id: str, object_key: str) -> list[UploadedPart]:
        try:
            upload_path = self._upload_path(storage_upload_id, object_key)
        except KeyError:
            return []
        parts = []
        for path in sorted(upload_path.glob("*.part"), key=lambda item: int(item.stem)):
            parts.append(_hash_file(path, int(path.stem)))
        return parts

    def complete_multipart(
        self,
        storage_upload_id: str,
        object_key: str,
        parts: list[UploadedPart],
    ) -> StoredObject:
        upload_path = self._upload_path(storage_upload_id, object_key)
        object_path = self._object_path(object_key)
        object_path.parent.mkdir(parents=True, exist_ok=True)
        checksum = hashlib.md5(usedforsecurity=False)
        size = 0
        with object_path.open("wb") as target:
            for part in parts:
                with (upload_path / f"{part.part_number}.part").open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                        checksum.update(chunk)
                        size += len(chunk)
        shutil.rmtree(upload_path)
        return StoredObject(object_key, size, checksum.hexdigest(), self.object_uri(object_key))

    def abort_multipart(self, storage_upload_id: str, object_key: str) -> None:
        try:
            upload_path = self._upload_path(storage_upload_id, object_key)
        except KeyError:
            return
        shutil.rmtree(upload_path)

    def delete_object(self, object_key: str) -> None:
        self._object_path(object_key).unlink(missing_ok=True)

    def object_exists(self, object_key: str) -> bool:
        return self._object_path(object_key).is_file()

    def iter_object(self, object_key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        with self._object_path(object_key).open("rb") as source:
            while chunk := source.read(chunk_size):
                yield chunk

    def object_uri(self, object_key: str) -> str:
        return self._object_path(object_key).resolve().as_uri()

    def _upload_path(self, storage_upload_id: str, object_key: str) -> Path:
        upload_path = self.parts_root / storage_upload_id
        metadata_path = upload_path / "object-key"
        if not metadata_path.is_file():
            raise KeyError("multipart upload not found")
        if metadata_path.read_text(encoding="utf-8") != object_key:
            raise ValueError("multipart upload does not belong to object key")
        return upload_path

    def _object_path(self, object_key: str) -> Path:
        _validate_object_key(object_key)
        return self.objects_root.joinpath(*PurePosixPath(object_key).parts)


class S3ObjectStore:
    """S3-compatible multipart adapter, including MinIO deployments."""

    def __init__(
        self,
        *,
        bucket: str,
        client: Any | None = None,
        presign_client: Any | None = None,
        endpoint_url: str | None = None,
        presign_endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        if client is None:
            client = _create_s3_client(
                endpoint_url=endpoint_url,
                access_key=access_key,
                secret_key=secret_key,
                region=region,
            )
        if presign_client is None:
            if presign_endpoint_url and presign_endpoint_url != endpoint_url:
                presign_client = _create_s3_client(
                    endpoint_url=presign_endpoint_url,
                    access_key=access_key,
                    secret_key=secret_key,
                    region=region,
                )
            else:
                presign_client = client
        self.client = client
        self.presign_client = presign_client
        self.bucket = bucket

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception as error:
            response = getattr(error, "response", {})
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = response.get("Error", {}).get("Code")
            if status not in {404, None} and code not in {"404", "NoSuchBucket"}:
                raise
            self.client.create_bucket(Bucket=self.bucket)

    def create_multipart(self, object_key: str, content_type: str) -> str:
        _validate_object_key(object_key)
        response = self.client.create_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            ContentType=content_type,
        )
        return str(response["UploadId"])

    def presign_part(
        self,
        storage_upload_id: str,
        object_key: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        _validate_object_key(object_key)
        return str(
            self.presign_client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self.bucket,
                    "Key": object_key,
                    "UploadId": storage_upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=expires_seconds,
            )
        )

    def list_parts(self, storage_upload_id: str, object_key: str) -> list[UploadedPart]:
        _validate_object_key(object_key)
        parts: list[UploadedPart] = []
        marker: int | None = None
        while True:
            request: dict[str, Any] = {
                "Bucket": self.bucket,
                "Key": object_key,
                "UploadId": storage_upload_id,
            }
            if marker is not None:
                request["PartNumberMarker"] = marker
            response = self.client.list_parts(**request)
            parts.extend(
                UploadedPart(
                    part_number=int(item["PartNumber"]),
                    size_bytes=int(item["Size"]),
                    etag=str(item["ETag"]),
                    checksum_sha256=item.get("ChecksumSHA256"),
                )
                for item in response.get("Parts", [])
            )
            if not response.get("IsTruncated"):
                break
            marker = int(response["NextPartNumberMarker"])
        return sorted(parts, key=lambda part: part.part_number)

    def complete_multipart(
        self,
        storage_upload_id: str,
        object_key: str,
        parts: list[UploadedPart],
    ) -> StoredObject:
        _validate_object_key(object_key)
        response = self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            UploadId=storage_upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": part.part_number, "ETag": part.etag}
                    for part in parts
                ]
            },
        )
        metadata = self.client.head_object(Bucket=self.bucket, Key=object_key)
        return StoredObject(
            object_key=object_key,
            size_bytes=int(metadata["ContentLength"]),
            etag=str(response.get("ETag", metadata.get("ETag", ""))),
            uri=self.object_uri(object_key),
        )

    def abort_multipart(self, storage_upload_id: str, object_key: str) -> None:
        _validate_object_key(object_key)
        self.client.abort_multipart_upload(
            Bucket=self.bucket, Key=object_key, UploadId=storage_upload_id
        )

    def delete_object(self, object_key: str) -> None:
        _validate_object_key(object_key)
        self.client.delete_object(Bucket=self.bucket, Key=object_key)

    def object_exists(self, object_key: str) -> bool:
        _validate_object_key(object_key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=object_key)
        except Exception as error:
            response = getattr(error, "response", {})
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = response.get("Error", {}).get("Code")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def iter_object(self, object_key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        _validate_object_key(object_key)
        response = self.client.get_object(Bucket=self.bucket, Key=object_key)
        yield from response["Body"].iter_chunks(chunk_size=chunk_size)

    def object_uri(self, object_key: str) -> str:
        _validate_object_key(object_key)
        return f"s3://{self.bucket}/{object_key}"


def _validate_object_key(object_key: str) -> None:
    path = PurePosixPath(object_key)
    if path.is_absolute() or ".." in path.parts or not object_key:
        raise ValueError("object key must be an opaque relative key")


def _create_s3_client(
    *, endpoint_url: str | None, access_key: str | None, secret_key: str | None, region: str
) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _hash_file(path: Path, part_number: int) -> UploadedPart:
    checksum = hashlib.sha256()
    etag = hashlib.md5(usedforsecurity=False)
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            checksum.update(chunk)
            etag.update(chunk)
            size += len(chunk)
    return UploadedPart(part_number, size, etag.hexdigest(), checksum.hexdigest())
