"""Resumable multipart upload coordination."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, Field

from football_intelligence.domain import JobRecord
from football_intelligence.object_store import ObjectStore, UploadedPart
from football_intelligence.persistence.protocols import JobStore

PART_SIZE_BYTES = 16 * 1024 * 1024
MAX_UPLOAD_BYTES = 12 * 1024**3
DEFAULT_UPLOAD_EXPIRY = timedelta(hours=24)
_CONTENT_TYPES = {".mp4": "video/mp4", ".mkv": "video/x-matroska", ".mov": "video/quicktime"}


class UploadError(RuntimeError):
    pass


class UploadNotFound(UploadError):
    pass


class UploadForbidden(UploadError):
    pass


class UploadExpired(UploadError):
    pass


class UploadConflict(UploadError):
    pass


class CompletedPart(BaseModel):
    part_number: int = Field(ge=1)
    etag: str = Field(min_length=1)


class PresignedPart(BaseModel):
    part_number: int
    expected_size_bytes: int
    url: str


class MultipartUpload(BaseModel):
    id: str
    owner_id: str
    original_filename: str
    object_key: str
    storage_upload_id: str
    size_bytes: int
    part_size_bytes: int
    checksum_sha256: str
    expires_at: datetime
    status: str = "active"
    job_id: str | None = None
    uploaded_parts: list[UploadedPart] = Field(default_factory=list)


class MultipartUploadService:
    def __init__(self, *, object_store: ObjectStore, job_store: JobStore) -> None:
        self.object_store = object_store
        self.job_store = job_store
        self._uploads: dict[str, MultipartUpload] = {}
        self._lock = RLock()

    def create_upload(
        self,
        *,
        owner_id: str,
        filename: str,
        size_bytes: int,
        checksum_sha256: str,
        now: datetime | None = None,
        expires_in: timedelta = DEFAULT_UPLOAD_EXPIRY,
    ) -> MultipartUpload:
        clean_filename = Path(filename).name
        extension = Path(clean_filename).suffix.lower()
        if extension not in _CONTENT_TYPES:
            raise ValueError("upload must be an MP4, MKV, or MOV file")
        if not 0 < size_bytes <= MAX_UPLOAD_BYTES:
            raise ValueError("upload size must be between 1 byte and 12 GiB")
        if len(checksum_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in checksum_sha256
        ):
            raise ValueError("checksum_sha256 must be 64 lowercase hexadecimal characters")
        if not owner_id:
            raise ValueError("owner_id is required")
        current_time = now or datetime.now(UTC)
        upload_id = str(uuid4())
        object_key = f"uploads/{upload_id}/source{extension}"
        storage_upload_id = self.object_store.create_multipart(
            object_key, _CONTENT_TYPES[extension]
        )
        upload = MultipartUpload(
            id=upload_id,
            owner_id=owner_id,
            original_filename=clean_filename,
            object_key=object_key,
            storage_upload_id=storage_upload_id,
            size_bytes=size_bytes,
            part_size_bytes=PART_SIZE_BYTES,
            checksum_sha256=checksum_sha256,
            expires_at=current_time + expires_in,
        )
        with self._lock:
            self._uploads[upload.id] = upload
        return upload

    def presign_part(
        self,
        upload_id: str,
        owner_id: str,
        part_number: int,
        *,
        now: datetime | None = None,
    ) -> PresignedPart:
        with self._lock:
            upload = self._active_upload(upload_id, owner_id, now)
            part_count = math.ceil(upload.size_bytes / PART_SIZE_BYTES)
            if not 1 <= part_number <= part_count:
                raise ValueError("part number is outside the upload range")
            expected_size = (
                PART_SIZE_BYTES
                if part_number < part_count
                else upload.size_bytes - PART_SIZE_BYTES * (part_count - 1)
            )
            expires_seconds = max(
                1,
                int((upload.expires_at - (now or datetime.now(UTC))).total_seconds()),
            )
            url = self.object_store.presign_part(
                upload.storage_upload_id,
                upload.object_key,
                part_number,
                expires_seconds,
            )
            return PresignedPart(
                part_number=part_number,
                expected_size_bytes=expected_size,
                url=url,
            )

    def get_upload(
        self,
        upload_id: str,
        owner_id: str,
        *,
        now: datetime | None = None,
    ) -> MultipartUpload:
        with self._lock:
            upload = self._owned_upload(upload_id, owner_id)
            if upload.status == "aborted":
                raise UploadConflict("upload was aborted")
            if upload.status == "failed":
                raise UploadConflict("upload validation failed")
            if upload.status == "active":
                self._expire_if_needed(upload, now)
                parts = self.object_store.list_parts(
                    upload.storage_upload_id, upload.object_key
                )
            else:
                parts = upload.uploaded_parts
            return upload.model_copy(update={"uploaded_parts": parts})

    def complete_upload(
        self,
        upload_id: str,
        owner_id: str,
        completed_parts: list[CompletedPart],
        *,
        now: datetime | None = None,
    ) -> JobRecord:
        with self._lock:
            upload = self._owned_upload(upload_id, owner_id)
            if upload.status == "completed" and upload.job_id is not None:
                return self.job_store.get(upload.job_id)
            if upload.status == "validated":
                stored_parts = upload.uploaded_parts
            else:
                upload = self._active_upload(upload_id, owner_id, now)
                stored_parts = self.object_store.list_parts(
                    upload.storage_upload_id, upload.object_key
                )
                self._validate_parts(upload, completed_parts, stored_parts)
                self.object_store.complete_multipart(
                    upload.storage_upload_id, upload.object_key, stored_parts
                )
                actual_checksum = hashlib.sha256()
                actual_size = 0
                for chunk in self.object_store.iter_object(upload.object_key):
                    actual_checksum.update(chunk)
                    actual_size += len(chunk)
                if actual_size != upload.size_bytes:
                    self._reject_completed_object(upload, "size")
                if actual_checksum.hexdigest() != upload.checksum_sha256:
                    self._reject_completed_object(upload, "checksum")
                upload = upload.model_copy(
                    update={"status": "validated", "uploaded_parts": stored_parts}
                )
                self._uploads[upload.id] = upload
            job = self.job_store.create(
                self.object_store.object_uri(upload.object_key),
                upload.original_filename,
            )
            finished = upload.model_copy(
                update={
                    "status": "completed",
                    "job_id": job.id,
                    "uploaded_parts": stored_parts,
                }
            )
            self._uploads[upload.id] = finished
            return job

    def abort_upload(
        self,
        upload_id: str,
        owner_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        with self._lock:
            upload = self._owned_upload(upload_id, owner_id)
            if upload.status == "completed":
                raise UploadConflict("completed upload cannot be aborted")
            if upload.status == "active":
                self._expire_if_needed(upload, now)
                self.object_store.abort_multipart(
                    upload.storage_upload_id, upload.object_key
                )
            elif upload.status == "validated":
                self.object_store.delete_object(upload.object_key)
            self._uploads[upload.id] = upload.model_copy(update={"status": "aborted"})

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(UTC)
        cleaned = 0
        with self._lock:
            for upload in list(self._uploads.values()):
                if upload.status == "active" and current_time >= upload.expires_at:
                    self.object_store.abort_multipart(
                        upload.storage_upload_id, upload.object_key
                    )
                    self._uploads[upload.id] = upload.model_copy(update={"status": "expired"})
                    cleaned += 1
        return cleaned

    def _validate_parts(
        self,
        upload: MultipartUpload,
        completed_parts: list[CompletedPart],
        stored_parts: list[UploadedPart],
    ) -> None:
        part_count = math.ceil(upload.size_bytes / PART_SIZE_BYTES)
        if len(completed_parts) != part_count or len(stored_parts) != part_count:
            raise UploadConflict("all upload parts are required before completion")
        completed_by_number = {part.part_number: part for part in completed_parts}
        stored_by_number = {part.part_number: part for part in stored_parts}
        if set(completed_by_number) != set(range(1, part_count + 1)):
            raise UploadConflict("completed part numbers do not match the upload")
        if set(stored_by_number) != set(range(1, part_count + 1)):
            raise UploadConflict("stored part numbers do not match the upload")
        for part_number in range(1, part_count + 1):
            completed = completed_by_number[part_number]
            stored = stored_by_number[part_number]
            if completed.etag.strip('"') != stored.etag.strip('"'):
                raise UploadConflict(f"ETag mismatch for part {part_number}")
            expected_size = (
                PART_SIZE_BYTES
                if part_number < part_count
                else upload.size_bytes - PART_SIZE_BYTES * (part_count - 1)
            )
            if stored.size_bytes != expected_size:
                raise UploadConflict(f"size mismatch for part {part_number}")

    def _reject_completed_object(self, upload: MultipartUpload, field: str) -> None:
        self.object_store.delete_object(upload.object_key)
        self._uploads[upload.id] = upload.model_copy(update={"status": "failed"})
        raise UploadConflict(f"completed object {field} does not match declaration")

    def _owned_upload(self, upload_id: str, owner_id: str) -> MultipartUpload:
        try:
            upload = self._uploads[upload_id]
        except KeyError as exc:
            raise UploadNotFound(upload_id) from exc
        if upload.owner_id != owner_id:
            raise UploadForbidden("upload belongs to another owner")
        return upload

    def _active_upload(
        self, upload_id: str, owner_id: str, now: datetime | None
    ) -> MultipartUpload:
        upload = self._owned_upload(upload_id, owner_id)
        if upload.status != "active":
            raise UploadConflict(f"upload is {upload.status}")
        self._expire_if_needed(upload, now)
        return upload

    def _expire_if_needed(self, upload: MultipartUpload, now: datetime | None) -> None:
        current_time = now or datetime.now(UTC)
        if current_time >= upload.expires_at:
            self.object_store.abort_multipart(upload.storage_upload_id, upload.object_key)
            self._uploads[upload.id] = upload.model_copy(update={"status": "expired"})
            raise UploadExpired("upload session expired")
