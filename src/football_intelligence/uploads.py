"""Durable resumable multipart upload coordination."""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock, RLock
from uuid import uuid4

from pydantic import BaseModel, Field

from football_intelligence.domain import (
    JobRecord,
    UploadPart,
    UploadSession,
    UploadStatus,
)
from football_intelligence.object_store import (
    MultipartCompletionUncertain,
    MultipartUploadNotFound,
    ObjectNotFound,
    ObjectStore,
    UploadedPart,
)
from football_intelligence.persistence.protocols import JobStore, UploadStore
from football_intelligence.persistence.records import UploadRecord
from football_intelligence.persistence.uploads import (
    InMemoryUploadRepository,
    UploadRecordNotFound,
)

PART_SIZE_BYTES = 16 * 1024 * 1024
MAX_UPLOAD_BYTES = 12 * 1024**3
DEFAULT_UPLOAD_EXPIRY = timedelta(hours=24)
_CONTENT_TYPES = {".mp4": "video/mp4", ".mkv": "video/x-matroska", ".mov": "video/quicktime"}
_EXPIRABLE = {
    UploadStatus.ACTIVE,
    UploadStatus.COMPLETING,
    UploadStatus.VALIDATED,
}
_CLEANUP_PENDING = {
    UploadStatus.ABORTED,
    UploadStatus.FAILED,
    UploadStatus.EXPIRED,
}
_LOGGER = logging.getLogger(__name__)


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
    required_headers: dict[str, str]


class MultipartUploadService:
    def __init__(
        self,
        *,
        object_store: ObjectStore,
        job_store: JobStore,
        upload_store: UploadStore | None = None,
    ) -> None:
        self.object_store = object_store
        self.job_store = job_store
        self.upload_store = upload_store or InMemoryUploadRepository()
        self._locks_guard = Lock()
        self._upload_locks: dict[str, RLock] = {}

    def create_upload(
        self,
        *,
        owner_id: str,
        filename: str,
        size_bytes: int,
        checksum_sha256: str,
        now: datetime | None = None,
        expires_in: timedelta = DEFAULT_UPLOAD_EXPIRY,
    ) -> UploadSession:
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
        current_time = _now(now)
        if expires_in <= timedelta(0):
            raise ValueError("expires_in must be positive")
        upload_id = str(uuid4())
        object_key = f"uploads/{upload_id}/source{extension}"
        storage_upload_id = self.object_store.create_multipart(
            object_key, _CONTENT_TYPES[extension]
        )
        record = UploadRecord(
            id=upload_id,
            owner_id=owner_id,
            storage_upload_id=storage_upload_id,
            object_key=object_key,
            original_filename=clean_filename,
            size_bytes=size_bytes,
            part_size_bytes=PART_SIZE_BYTES,
            checksum_sha256=checksum_sha256,
            expires_at=current_time + expires_in,
            status=UploadStatus.ACTIVE,
            planned_job_id=upload_id,
            completion_parts=[],
            validated_parts=[],
            object_size_bytes=None,
            object_checksum_sha256=None,
            object_etag=None,
            job_id=None,
            cleanup_completed_at=None,
            version=0,
            created_at=current_time,
            updated_at=current_time,
        )
        try:
            created = self.upload_store.create(record)
        except Exception:
            self.object_store.abort_multipart(storage_upload_id, object_key)
            raise
        return _public_session(created)

    def presign_part(
        self,
        upload_id: str,
        owner_id: str,
        part_number: int,
        *,
        checksum_sha256: str,
        now: datetime | None = None,
    ) -> PresignedPart:
        if len(checksum_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in checksum_sha256
        ):
            raise ValueError("checksum_sha256 must be 64 lowercase hexadecimal characters")
        with self._lock_for(upload_id):
            upload = self._active_upload(upload_id, owner_id, now)
            part_count = math.ceil(upload.size_bytes / PART_SIZE_BYTES)
            if not 1 <= part_number <= part_count:
                raise ValueError("part number is outside the upload range")
            expected_size = _expected_part_size(upload, part_number, part_count)
            expires_seconds = max(
                1,
                int((upload.expires_at - _now(now)).total_seconds()),
            )
            url = self.object_store.presign_part(
                upload.storage_upload_id,
                upload.object_key,
                part_number,
                expires_seconds,
                expected_size_bytes=expected_size,
                checksum_sha256=checksum_sha256,
            )
            return PresignedPart(
                part_number=part_number,
                expected_size_bytes=expected_size,
                url=url,
                required_headers={
                    "Content-Length": str(expected_size),
                    "x-amz-checksum-sha256": _checksum_base64(checksum_sha256),
                },
            )

    def get_upload(
        self,
        upload_id: str,
        owner_id: str,
        *,
        now: datetime | None = None,
    ) -> UploadSession:
        with self._lock_for(upload_id):
            upload = self._owned_upload(upload_id, owner_id)
            self._expire_if_needed(upload, now)
            if upload.status is UploadStatus.ABORTED:
                raise UploadConflict("upload was aborted")
            if upload.status is UploadStatus.FAILED:
                raise UploadConflict("upload validation failed")
            if upload.status is UploadStatus.EXPIRED:
                raise UploadExpired("upload session expired")
            if upload.status is UploadStatus.ACTIVE:
                parts = _domain_parts(
                    self.object_store.list_parts(
                        upload.storage_upload_id, upload.object_key
                    )
                )
            else:
                parts = upload.validated_parts or upload.completion_parts
            return _public_session(upload, parts)

    def complete_upload(
        self,
        upload_id: str,
        owner_id: str,
        completed_parts: list[CompletedPart],
        *,
        now: datetime | None = None,
    ) -> JobRecord:
        with self._lock_for(upload_id):
            upload = self._owned_upload(upload_id, owner_id)
            self._expire_if_needed(upload, now)
            if upload.status is UploadStatus.COMPLETED and upload.job_id is not None:
                return self.job_store.get(upload.job_id)
            if upload.status is UploadStatus.ACTIVE:
                stored_parts = self.object_store.list_parts(
                    upload.storage_upload_id, upload.object_key
                )
                self._validate_parts(upload, completed_parts, stored_parts)
                intent = _domain_parts(stored_parts)
                claimed = self.upload_store.compare_and_set(
                    upload.id,
                    expected_status=UploadStatus.ACTIVE,
                    expected_version=upload.version,
                    values={
                        "status": UploadStatus.COMPLETING.value,
                        "completion_parts": intent,
                    },
                )
                upload = claimed or self._owned_upload(upload_id, owner_id)
            if upload.status is UploadStatus.COMPLETING:
                upload = self._complete_and_validate(upload)
            if upload.status is UploadStatus.VALIDATED:
                finalizing = self.upload_store.compare_and_set(
                    upload.id,
                    expected_status=UploadStatus.VALIDATED,
                    expected_version=upload.version,
                    values={"status": UploadStatus.FINALIZING.value},
                )
                upload = finalizing or self._owned_upload(upload_id, owner_id)
            if upload.status is not UploadStatus.FINALIZING:
                if upload.status is UploadStatus.COMPLETED and upload.job_id:
                    return self.job_store.get(upload.job_id)
                raise UploadConflict(f"upload is {upload.status.value}")
            job = self.job_store.create_with_id(
                upload.planned_job_id,
                self.object_store.object_uri(upload.object_key),
                upload.original_filename,
            )
            completed = self.upload_store.compare_and_set(
                upload.id,
                expected_status=UploadStatus.FINALIZING,
                expected_version=upload.version,
                values={
                    "status": UploadStatus.COMPLETED.value,
                    "job_id": job.id,
                },
            )
            if completed is None:
                current = self._owned_upload(upload.id, owner_id)
                if current.status is not UploadStatus.COMPLETED or current.job_id != job.id:
                    raise UploadConflict("upload state changed during job creation")
            return job

    def abort_upload(
        self,
        upload_id: str,
        owner_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        with self._lock_for(upload_id):
            upload = self._owned_upload(upload_id, owner_id)
            if upload.status is UploadStatus.COMPLETED:
                raise UploadConflict("completed upload cannot be aborted")
            if upload.status is UploadStatus.ABORTED:
                if upload.cleanup_completed_at is None:
                    self._try_cleanup(upload, _now(now))
                return
            if upload.status is UploadStatus.EXPIRED:
                return
            if upload.status is UploadStatus.FAILED:
                raise UploadConflict("failed upload cannot be aborted")
            self._expire_if_needed(upload, now)
            aborted = self.upload_store.compare_and_set(
                upload.id,
                expected_status=upload.status,
                expected_version=upload.version,
                values={"status": UploadStatus.ABORTED.value},
            )
            if aborted is None:
                raise UploadConflict("upload state changed during abort")
            self._try_cleanup(aborted, _now(now))

    def cleanup_expired(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> int:
        current_time = _now(now)
        cleaned = 0
        for candidate in self.upload_store.list_expired(now=current_time, limit=limit):
            with self._lock_for(candidate.id):
                current = self.upload_store.get(candidate.id)
                if current.status in _EXPIRABLE:
                    if current.expires_at > current_time:
                        continue
                    expired = self.upload_store.compare_and_set(
                        current.id,
                        expected_status=current.status,
                        expected_version=current.version,
                        values={"status": UploadStatus.EXPIRED.value},
                    )
                    if expired is None:
                        continue
                    current = expired
                elif current.status not in _CLEANUP_PENDING:
                    continue
                if current.cleanup_completed_at is not None:
                    continue
                if self._try_cleanup(current, current_time):
                    cleaned += 1
        return cleaned

    def _complete_and_validate(self, upload: UploadRecord) -> UploadRecord:
        stored_parts = [_stored_part(part) for part in upload.completion_parts]
        try:
            completed_object = self.object_store.complete_multipart(
                upload.storage_upload_id, upload.object_key, stored_parts
            )
        except (MultipartUploadNotFound, MultipartCompletionUncertain) as error:
            try:
                completed_object = self.object_store.stat_object(upload.object_key)
            except ObjectNotFound:
                raise UploadConflict(
                    "multipart completion outcome is not yet recoverable"
                ) from error
        if completed_object.size_bytes != upload.size_bytes:
            self._reject_completed_object(upload, "size")
        actual_checksum = hashlib.sha256()
        actual_size = 0
        for chunk in self.object_store.iter_object(upload.object_key):
            actual_checksum.update(chunk)
            actual_size += len(chunk)
        digest = actual_checksum.hexdigest()
        if actual_size != upload.size_bytes:
            self._reject_completed_object(upload, "size")
        if digest != upload.checksum_sha256:
            self._reject_completed_object(upload, "checksum")
        validated = self.upload_store.compare_and_set(
            upload.id,
            expected_status=UploadStatus.COMPLETING,
            expected_version=upload.version,
            values={
                "status": UploadStatus.VALIDATED.value,
                "validated_parts": upload.completion_parts,
                "object_size_bytes": actual_size,
                "object_checksum_sha256": digest,
                "object_etag": completed_object.etag,
            },
        )
        if validated is not None:
            return validated
        current = self.upload_store.get(upload.id)
        if current.status in {
            UploadStatus.VALIDATED,
            UploadStatus.FINALIZING,
            UploadStatus.COMPLETED,
        }:
            return current
        if current.status in {UploadStatus.EXPIRED, UploadStatus.ABORTED}:
            self.object_store.delete_object(upload.object_key)
        raise UploadConflict("upload state changed during object validation")

    def _validate_parts(
        self,
        upload: UploadRecord,
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
            expected_size = _expected_part_size(upload, part_number, part_count)
            if stored.size_bytes != expected_size:
                raise UploadConflict(f"size mismatch for part {part_number}")

    def _reject_completed_object(self, upload: UploadRecord, field: str) -> None:
        failed = self.upload_store.compare_and_set(
            upload.id,
            expected_status=UploadStatus.COMPLETING,
            expected_version=upload.version,
            values={"status": UploadStatus.FAILED.value},
        )
        if failed is None:
            raise UploadConflict("upload state changed during rejection")
        self._try_cleanup(failed, datetime.now(UTC))
        raise UploadConflict(f"completed object {field} does not match declaration")

    def _owned_upload(self, upload_id: str, owner_id: str) -> UploadRecord:
        try:
            upload = self.upload_store.get(upload_id)
        except UploadRecordNotFound as error:
            raise UploadNotFound(upload_id) from error
        if upload.owner_id != owner_id:
            raise UploadForbidden("upload belongs to another owner")
        return upload

    def _active_upload(
        self, upload_id: str, owner_id: str, now: datetime | None
    ) -> UploadRecord:
        upload = self._owned_upload(upload_id, owner_id)
        self._expire_if_needed(upload, now)
        if upload.status is not UploadStatus.ACTIVE:
            raise UploadConflict(f"upload is {upload.status.value}")
        return upload

    def _expire_if_needed(self, upload: UploadRecord, now: datetime | None) -> None:
        current_time = _now(now)
        if upload.status in _EXPIRABLE and current_time >= upload.expires_at:
            expired = self.upload_store.compare_and_set(
                upload.id,
                expected_status=upload.status,
                expected_version=upload.version,
                values={"status": UploadStatus.EXPIRED.value},
            )
            if expired is not None:
                self._try_cleanup(expired, current_time)
            raise UploadExpired("upload session expired")

    def _try_cleanup(self, upload: UploadRecord, cleaned_at: datetime) -> bool:
        try:
            self._cleanup_storage(upload)
        except Exception:
            _LOGGER.exception("upload cleanup failed for %s", upload.id)
            return False
        marked = self.upload_store.compare_and_set(
            upload.id,
            expected_status=upload.status,
            expected_version=upload.version,
            values={"cleanup_completed_at": cleaned_at},
        )
        return marked is not None

    def _cleanup_storage(self, upload: UploadRecord) -> None:
        self.object_store.abort_multipart(
            upload.storage_upload_id, upload.object_key
        )
        self.object_store.delete_object(upload.object_key)

    def _lock_for(self, upload_id: str) -> RLock:
        with self._locks_guard:
            return self._upload_locks.setdefault(upload_id, RLock())


def _public_session(
    record: UploadRecord, uploaded_parts: list[UploadPart] | None = None
) -> UploadSession:
    return UploadSession(
        id=record.id,
        job_id=record.job_id,
        object_key=record.object_key,
        original_filename=record.original_filename,
        size_bytes=record.size_bytes,
        part_size_bytes=record.part_size_bytes,
        checksum_sha256=record.checksum_sha256,
        expires_at=record.expires_at,
        status=record.status,
        version=record.version,
        uploaded_parts=uploaded_parts
        if uploaded_parts is not None
        else record.validated_parts,
    )


def _domain_parts(parts: list[UploadedPart]) -> list[UploadPart]:
    return [
        UploadPart(
            part_number=part.part_number,
            size_bytes=part.size_bytes,
            etag=part.etag,
            checksum_sha256=part.checksum_sha256,
        )
        for part in parts
    ]


def _stored_part(part: UploadPart) -> UploadedPart:
    return UploadedPart(
        part_number=part.part_number,
        size_bytes=part.size_bytes,
        etag=part.etag,
        checksum_sha256=part.checksum_sha256,
    )


def _expected_part_size(
    upload: UploadRecord, part_number: int, part_count: int
) -> int:
    if part_number < part_count:
        return PART_SIZE_BYTES
    return upload.size_bytes - PART_SIZE_BYTES * (part_count - 1)


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(UTC)


def _checksum_base64(checksum_sha256: str) -> str:
    from base64 import b64encode

    return b64encode(bytes.fromhex(checksum_sha256)).decode("ascii")
