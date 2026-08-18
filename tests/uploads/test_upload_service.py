from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from football_intelligence.object_store import InMemoryObjectStore
from football_intelligence.storage import JobRepository
from football_intelligence.uploads import (
    MAX_UPLOAD_BYTES,
    PART_SIZE_BYTES,
    CompletedPart,
    MultipartUploadService,
    UploadConflict,
    UploadExpired,
    UploadForbidden,
)


@pytest.fixture
def upload_system(tmp_path: Path):
    objects = InMemoryObjectStore()
    jobs = JobRepository(tmp_path / "jobs.db")
    service = MultipartUploadService(object_store=objects, job_store=jobs)
    return service, objects, jobs


def test_create_upload_validates_quota_extension_and_uses_opaque_key(upload_system):
    service, _, _ = upload_system

    upload = service.create_upload(
        owner_id="operator-1",
        filename="../../Final Match.MP4",
        size_bytes=PART_SIZE_BYTES,
        checksum_sha256="a" * 64,
    )

    assert upload.original_filename == "Final Match.MP4"
    assert upload.part_size_bytes == 16 * 1024 * 1024
    assert upload.object_key.startswith(f"uploads/{upload.id}/")
    assert "Final Match" not in upload.object_key
    with pytest.raises(ValueError, match="MP4, MKV, or MOV"):
        service.create_upload(
            owner_id="operator-1",
            filename="match.avi",
            size_bytes=1,
            checksum_sha256="a" * 64,
        )
    with pytest.raises(ValueError, match="12 GiB"):
        service.create_upload(
            owner_id="operator-1",
            filename="match.mkv",
            size_bytes=MAX_UPLOAD_BYTES + 1,
            checksum_sha256="a" * 64,
        )


def test_presign_enforces_ownership_part_range_and_expiry(upload_system):
    service, objects, _ = upload_system
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    upload = service.create_upload(
        owner_id="operator-1",
        filename="match.mov",
        size_bytes=PART_SIZE_BYTES + 3,
        checksum_sha256="a" * 64,
        now=now,
        expires_in=timedelta(minutes=5),
    )

    part = service.presign_part(upload.id, "operator-1", 2, now=now)

    assert part.part_number == 2
    assert part.expected_size_bytes == 3
    assert part.url.startswith("memory://")
    with pytest.raises(UploadForbidden):
        service.presign_part(upload.id, "operator-2", 1, now=now)
    with pytest.raises(ValueError, match="part number"):
        service.presign_part(upload.id, "operator-1", 3, now=now)
    with pytest.raises(UploadExpired):
        service.presign_part(upload.id, "operator-1", 1, now=now + timedelta(minutes=5))
    assert objects.list_parts(upload.storage_upload_id, upload.object_key) == []


def test_resume_and_complete_validate_etag_checksum_then_create_job(upload_system):
    service, objects, jobs = upload_system
    body = b"video-body"
    upload = service.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=len(body),
        checksum_sha256=sha256(body).hexdigest(),
    )
    stored_part = objects.upload_part(upload.storage_upload_id, upload.object_key, 1, body)

    resumed = service.get_upload(upload.id, "operator-1")
    assert resumed.uploaded_parts == [stored_part]
    assert jobs.list() == []

    job = service.complete_upload(
        upload.id,
        "operator-1",
        [CompletedPart(part_number=1, etag=stored_part.etag)],
    )

    assert job.original_filename == "match.mp4"
    assert job.source_path == f"memory://objects/{upload.object_key}"
    assert jobs.list() == [job]
    assert service.get_upload(upload.id, "operator-1").job_id == job.id


def test_complete_rejects_wrong_etag_or_checksum_without_creating_job(upload_system):
    service, objects, jobs = upload_system
    body = b"video-body"
    upload = service.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=len(body),
        checksum_sha256="0" * 64,
    )
    stored_part = objects.upload_part(upload.storage_upload_id, upload.object_key, 1, body)

    with pytest.raises(UploadConflict, match="ETag"):
        service.complete_upload(
            upload.id,
            "operator-1",
            [CompletedPart(part_number=1, etag="wrong")],
        )
    with pytest.raises(UploadConflict, match="checksum"):
        service.complete_upload(
            upload.id,
            "operator-1",
            [CompletedPart(part_number=1, etag=stored_part.etag)],
        )

    assert jobs.list() == []
    assert not objects.object_exists(upload.object_key)


def test_abort_and_expiry_remove_multipart_parts(upload_system):
    service, objects, _ = upload_system
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    upload = service.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=4,
        checksum_sha256=sha256(b"data").hexdigest(),
        now=now,
        expires_in=timedelta(seconds=30),
    )
    objects.upload_part(upload.storage_upload_id, upload.object_key, 1, b"data")

    service.abort_upload(upload.id, "operator-1", now=now)

    assert objects.list_parts(upload.storage_upload_id, upload.object_key) == []
    with pytest.raises(UploadConflict, match="aborted"):
        service.get_upload(upload.id, "operator-1", now=now)


def test_validated_object_can_retry_transient_job_creation_without_reupload(tmp_path: Path):
    objects = InMemoryObjectStore()
    jobs = JobRepository(tmp_path / "jobs.db")

    class FailOnceJobStore:
        def __init__(self):
            self.failed = False

        def create(self, source_path, original_filename):
            if not self.failed:
                self.failed = True
                raise RuntimeError("database temporarily unavailable")
            return jobs.create(source_path, original_filename)

        def get(self, job_id):
            return jobs.get(job_id)

    service = MultipartUploadService(
        object_store=objects, job_store=FailOnceJobStore()
    )
    body = b"video-body"
    upload = service.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=len(body),
        checksum_sha256=sha256(body).hexdigest(),
    )
    stored = objects.upload_part(upload.storage_upload_id, upload.object_key, 1, body)
    completed_parts = [CompletedPart(part_number=1, etag=stored.etag)]

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        service.complete_upload(upload.id, "operator-1", completed_parts)

    job = service.complete_upload(upload.id, "operator-1", completed_parts)

    assert objects.object_exists(upload.object_key)
    assert jobs.list() == [job]
