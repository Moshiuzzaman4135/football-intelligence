"""Durable upload-session repositories with compare-and-set transitions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from threading import RLock

from sqlalchemy import Engine, and_, or_, select, update
from sqlalchemy.orm import sessionmaker

from football_intelligence.domain import UploadPart, UploadStatus
from football_intelligence.persistence.models import UploadRow
from football_intelligence.persistence.records import UploadRecord


class UploadRecordNotFound(KeyError):
    pass


class SQLAlchemyUploadRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def create(self, upload: UploadRecord) -> UploadRecord:
        with self._sessions.begin() as session:
            session.add(_upload_row(upload))
        return self.get(upload.id)

    def get(self, upload_id: str) -> UploadRecord:
        with self._sessions() as session:
            row = session.get(UploadRow, upload_id)
            if row is None:
                raise UploadRecordNotFound(upload_id)
            return _upload_record(row)

    def compare_and_set(
        self,
        upload_id: str,
        *,
        expected_status: UploadStatus,
        expected_version: int,
        values: dict[str, object],
    ) -> UploadRecord | None:
        updates = _serialized_values(values)
        updates["version"] = expected_version + 1
        updates["updated_at"] = datetime.now(UTC)
        with self._sessions.begin() as session:
            result = session.execute(
                update(UploadRow)
                .where(
                    UploadRow.id == upload_id,
                    UploadRow.status == expected_status.value,
                    UploadRow.version == expected_version,
                )
                .values(**updates)
            )
            if result.rowcount != 1:
                return None
        return self.get(upload_id)

    def list_expired(self, *, now: datetime, limit: int) -> list[UploadRecord]:
        with self._sessions() as session:
            rows = session.scalars(
                select(UploadRow)
                .where(
                    or_(
                        and_(
                            UploadRow.expires_at <= now,
                            UploadRow.status.in_(
                                (
                                    UploadStatus.ACTIVE.value,
                                    UploadStatus.COMPLETING.value,
                                    UploadStatus.VALIDATED.value,
                                )
                            ),
                        ),
                        and_(
                            UploadRow.status.in_(
                                (
                                    UploadStatus.ABORTED.value,
                                    UploadStatus.FAILED.value,
                                    UploadStatus.EXPIRED.value,
                                )
                            ),
                            UploadRow.cleanup_completed_at.is_(None),
                        ),
                    ),
                )
                .order_by(UploadRow.expires_at, UploadRow.id)
                .limit(limit)
            ).all()
            return [_upload_record(row) for row in rows]


class InMemoryUploadRepository:
    def __init__(self) -> None:
        self._records: dict[str, UploadRecord] = {}
        self._lock = RLock()

    def create(self, upload: UploadRecord) -> UploadRecord:
        with self._lock:
            if upload.id in self._records:
                raise ValueError(f"upload {upload.id} already exists")
            self._records[upload.id] = upload.model_copy(deep=True)
            return self._records[upload.id].model_copy(deep=True)

    def get(self, upload_id: str) -> UploadRecord:
        with self._lock:
            try:
                return self._records[upload_id].model_copy(deep=True)
            except KeyError as error:
                raise UploadRecordNotFound(upload_id) from error

    def compare_and_set(
        self,
        upload_id: str,
        *,
        expected_status: UploadStatus,
        expected_version: int,
        values: dict[str, object],
    ) -> UploadRecord | None:
        with self._lock:
            current = self.get(upload_id)
            if current.status is not expected_status or current.version != expected_version:
                return None
            updated = UploadRecord.model_validate(
                {
                    **current.model_dump(),
                    **values,
                    "version": current.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._records[upload_id] = updated
            return updated.model_copy(deep=True)

    def list_expired(self, *, now: datetime, limit: int) -> list[UploadRecord]:
        with self._lock:
            matches = [
                item.model_copy(deep=True)
                for item in self._records.values()
                if (
                    item.expires_at <= now
                    and item.status
                    in {
                        UploadStatus.ACTIVE,
                        UploadStatus.COMPLETING,
                        UploadStatus.VALIDATED,
                    }
                )
                or (
                    item.status
                    in {
                        UploadStatus.ABORTED,
                        UploadStatus.FAILED,
                        UploadStatus.EXPIRED,
                    }
                    and item.cleanup_completed_at is None
                )
            ]
        return sorted(matches, key=lambda item: (item.expires_at, item.id))[:limit]


def _upload_row(record: UploadRecord) -> UploadRow:
    values = record.model_dump(exclude={"completion_parts", "validated_parts"})
    values["status"] = record.status.value
    values["completion_parts_json"] = _parts_json(record.completion_parts)
    values["validated_parts_json"] = _parts_json(record.validated_parts)
    return UploadRow(**values)


def _upload_record(row: UploadRow) -> UploadRecord:
    return UploadRecord(
        id=row.id,
        owner_id=row.owner_id,
        storage_upload_id=row.storage_upload_id,
        object_key=row.object_key,
        original_filename=row.original_filename,
        size_bytes=row.size_bytes,
        part_size_bytes=row.part_size_bytes,
        checksum_sha256=row.checksum_sha256,
        expires_at=_as_utc(row.expires_at),
        status=UploadStatus(row.status),
        planned_job_id=row.planned_job_id,
        completion_parts=_parts(row.completion_parts_json),
        validated_parts=_parts(row.validated_parts_json),
        object_size_bytes=row.object_size_bytes,
        object_checksum_sha256=row.object_checksum_sha256,
        object_etag=row.object_etag,
        job_id=row.job_id,
        cleanup_completed_at=(
            _as_utc(row.cleanup_completed_at) if row.cleanup_completed_at else None
        ),
        version=row.version,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _serialized_values(values: dict[str, object]) -> dict[str, object]:
    serialized = dict(values)
    if status := serialized.get("status"):
        serialized["status"] = status.value if isinstance(status, UploadStatus) else status
    for field in ("completion_parts", "validated_parts"):
        if field in serialized:
            serialized[f"{field}_json"] = _parts_json(serialized.pop(field))
    return serialized


def _parts_json(parts: object) -> str:
    return json.dumps(
        [
            part.model_dump(mode="json")
            if isinstance(part, UploadPart)
            else UploadPart.model_validate(part).model_dump(mode="json")
            for part in parts
        ]
    )


def _parts(value: str) -> list[UploadPart]:
    return [UploadPart.model_validate(item) for item in json.loads(value)]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
