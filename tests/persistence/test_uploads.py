from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from football_intelligence.domain import UploadPart, UploadStatus
from football_intelligence.persistence import (
    SQLAlchemyUploadRepository,
    UploadRecord,
    create_persistence_engine,
    create_schema,
)


def _repository(path: Path) -> SQLAlchemyUploadRepository:
    engine = create_persistence_engine(f"sqlite:///{path}")
    create_schema(engine)
    return SQLAlchemyUploadRepository(engine)


def _record(now: datetime, upload_id: str = "upload-1") -> UploadRecord:
    return UploadRecord(
        id=upload_id,
        owner_id="operator-1",
        storage_upload_id="storage-1",
        object_key=f"uploads/{upload_id}/source.mp4",
        original_filename="match.mp4",
        size_bytes=16 * 1024 * 1024,
        part_size_bytes=16 * 1024 * 1024,
        checksum_sha256="a" * 64,
        expires_at=now + timedelta(hours=1),
        status=UploadStatus.ACTIVE,
        planned_job_id=upload_id,
        completion_parts=[],
        validated_parts=[],
        object_size_bytes=None,
        object_checksum_sha256=None,
        object_etag=None,
        job_id=None,
        version=0,
        created_at=now,
        updated_at=now,
    )


def test_upload_repository_round_trips_private_state_and_cas(tmp_path: Path):
    repository = _repository(tmp_path / "uploads.db")
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    created = repository.create(_record(now))
    intent = [
        UploadPart(
            part_number=1,
            size_bytes=16 * 1024 * 1024,
            etag='"etag-1"',
            checksum_sha256="b" * 64,
        )
    ]

    completing = repository.compare_and_set(
        created.id,
        expected_status=UploadStatus.ACTIVE,
        expected_version=created.version,
        values={
            "status": UploadStatus.COMPLETING.value,
            "completion_parts": intent,
        },
    )
    stale = repository.compare_and_set(
        created.id,
        expected_status=UploadStatus.ACTIVE,
        expected_version=created.version,
        values={"status": UploadStatus.ABORTED.value},
    )

    assert completing is not None
    assert completing.version == 1
    assert completing.completion_parts == intent
    assert completing.storage_upload_id == "storage-1"
    assert stale is None
    assert repository.get(created.id) == completing


def test_two_repository_instances_have_one_cas_winner(tmp_path: Path):
    path = tmp_path / "uploads.db"
    first = _repository(path)
    second = _repository(path)
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    record = first.create(_record(now))

    def claim(repository):
        return repository.compare_and_set(
            record.id,
            expected_status=UploadStatus.ACTIVE,
            expected_version=0,
            values={"status": UploadStatus.COMPLETING.value},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, (first, second)))

    assert len([result for result in results if result is not None]) == 1
    assert first.get(record.id).status is UploadStatus.COMPLETING
    assert first.get(record.id).version == 1


def test_expired_listing_includes_inflight_validation_but_not_completed(tmp_path: Path):
    repository = _repository(tmp_path / "uploads.db")
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    for position, status in enumerate(
        (
            UploadStatus.ACTIVE,
            UploadStatus.COMPLETING,
            UploadStatus.VALIDATED,
            UploadStatus.COMPLETED,
        )
    ):
        record = _record(now - timedelta(hours=2), f"upload-{position}").model_copy(
            update={
                "status": status,
                "expires_at": now - timedelta(hours=1),
                "storage_upload_id": f"storage-{position}",
            }
        )
        repository.create(record)

    expired = repository.list_expired(now=now, limit=10)

    assert {item.status for item in expired} == {
        UploadStatus.ACTIVE,
        UploadStatus.COMPLETING,
        UploadStatus.VALIDATED,
    }
