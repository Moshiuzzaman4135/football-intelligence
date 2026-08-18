"""SQLAlchemy repositories usable with SQLite and PostgreSQL engines."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, create_engine, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from football_intelligence.domain import (
    FootballEvent,
    JobMetadata,
    JobRecord,
    JobStatus,
    ModelMetadata,
    StageName,
    StageStatus,
    TrackObservation,
    TrackSummary,
    VideoMetadata,
)
from football_intelligence.persistence.models import (
    Base,
    JobMetadataRow,
    JobRow,
    PayloadRow,
    StageRow,
)
from football_intelligence.persistence.records import StageRecord
from football_intelligence.storage import ALLOWED_TRANSITIONS, InvalidJobTransition, JobNotFound


class RawObservationPersistenceError(ValueError):
    pass


def create_persistence_engine(database_url: str) -> Engine:
    options: dict[str, object] = {"future": True}
    if database_url.startswith("sqlite:"):
        options["connect_args"] = {"check_same_thread": False}
    return create_engine(database_url, **options)


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)


class SQLAlchemyJobRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def create(self, source_path: str, original_filename: str) -> JobRecord:
        return self.create_with_id(str(uuid4()), source_path, original_filename)

    def create_with_id(
        self, job_id: str, source_path: str, original_filename: str
    ) -> JobRecord:
        now = datetime.now(UTC)
        row = JobRow(
            id=job_id,
            source_path=source_path,
            original_filename=original_filename,
            status=JobStatus.CREATED.value,
            progress=0,
            metrics_json="{}",
            created_at=now,
            updated_at=now,
            version=0,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except IntegrityError:
            existing = self.get(job_id)
            if (
                existing.source_path != source_path
                or existing.original_filename != original_filename
            ):
                raise InvalidJobTransition(
                    f"job id {job_id} belongs to another source"
                ) from None
            return existing
        return _job_record(row)

    def get(self, job_id: str) -> JobRecord:
        with self._sessions() as session:
            row = session.get(JobRow, job_id)
            if row is None:
                raise JobNotFound(job_id)
            return _job_record(row, _metadata_record(session.get(JobMetadataRow, job_id)))

    def list(self) -> list[JobRecord]:
        with self._sessions() as session:
            rows = session.scalars(select(JobRow).order_by(JobRow.created_at.desc())).all()
            return [
                _job_record(row, _metadata_record(session.get(JobMetadataRow, row.id)))
                for row in rows
            ]

    def transition(
        self,
        job_id: str,
        target: JobStatus,
        *,
        error: str | None = None,
        output_path: str | None = None,
        metrics: dict[str, float | int | str] | None = None,
    ) -> JobRecord:
        with self._sessions.begin() as session:
            row = _require_job(session, job_id)
            current = JobStatus(row.status)
            if target not in ALLOWED_TRANSITIONS[current]:
                raise InvalidJobTransition(f"{current.value} -> {target.value}")
            values: dict[str, object] = {
                "status": target.value,
                "progress": 100 if target is JobStatus.COMPLETED else row.progress,
                "updated_at": datetime.now(UTC),
            }
            if error is not None:
                values["error"] = error
            if output_path is not None:
                values["output_path"] = output_path
            if metrics is not None:
                values["metrics_json"] = json.dumps(metrics)
            _cas_job(session, row, values, f"{current.value} -> {target.value}")
        return self.get(job_id)

    def update_progress(self, job_id: str, progress: int) -> JobRecord:
        if not 0 <= progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        with self._sessions.begin() as session:
            row = _require_job(session, job_id)
            if progress < row.progress:
                raise ValueError("progress cannot decrease")
            _cas_job(
                session,
                row,
                {"progress": progress, "updated_at": datetime.now(UTC)},
                "progress update",
            )
        return self.get(job_id)

    def complete_or_stop(
        self,
        job_id: str,
        *,
        output_path: str,
        metrics: dict[str, float | int | str],
    ) -> JobRecord:
        with self._sessions.begin() as session:
            row = _require_job(session, job_id)
            current = JobStatus(row.status)
            if current is JobStatus.RUNNING:
                values: dict[str, object] = {
                    "status": JobStatus.COMPLETED.value,
                    "progress": 100,
                    "output_path": output_path,
                    "metrics_json": json.dumps(metrics),
                    "updated_at": datetime.now(UTC),
                }
            elif current is JobStatus.STOPPING:
                values = {
                    "status": JobStatus.STOPPED.value,
                    "updated_at": datetime.now(UTC),
                }
            else:
                raise InvalidJobTransition(f"cannot finalize {current.value} job")
            _cas_job(session, row, values, "finalization")
        return self.get(job_id)

    def save_events(self, job_id: str, events: list[FootballEvent]) -> None:
        self._save_models("events", job_id, events)

    def get_events(self, job_id: str) -> list[FootballEvent]:
        return [
            FootballEvent.model_validate_json(item) for item in self._load_json("events", job_id)
        ]

    def save_tracks(self, job_id: str, tracks: list[TrackObservation]) -> None:
        del job_id, tracks
        raise RawObservationPersistenceError(
            "raw track observations require an external artifact store"
        )

    def get_tracks(self, job_id: str) -> list[TrackObservation]:
        self.get(job_id)
        return []

    def save_track_summaries(self, job_id: str, summaries: list[TrackSummary]) -> None:
        self._save_models("track_summaries", job_id, summaries)

    def get_track_summaries(self, job_id: str) -> list[TrackSummary]:
        return [
            TrackSummary.model_validate_json(item)
            for item in self._load_json("track_summaries", job_id)
        ]

    def save_job_metadata(
        self,
        job_id: str,
        *,
        source: VideoMetadata | None = None,
        output: VideoMetadata | None = None,
        model: ModelMetadata | None = None,
    ) -> JobMetadata:
        with self._sessions.begin() as session:
            row = _require_job(session, job_id)
            metadata_row = session.get(JobMetadataRow, job_id)
            current = _metadata_record(metadata_row)
            metadata = current.model_copy(
                update={
                    "source": source if source is not None else current.source,
                    "output": output if output is not None else current.output,
                    "model": model if model is not None else current.model,
                }
            )
            _cas_job(session, row, {}, "metadata update")
            values = _metadata_values(metadata)
            if metadata_row is None:
                session.add(JobMetadataRow(job_id=job_id, **values))
            else:
                for name, value in values.items():
                    setattr(metadata_row, name, value)
        return metadata

    def import_job(
        self,
        job: JobRecord,
        *,
        events: list[FootballEvent],
        track_summaries: list[TrackSummary],
    ) -> bool:
        """Insert one immutable legacy snapshot, returning false for an existing job."""
        with self._sessions() as session, session.begin():
            try:
                with session.begin_nested():
                    session.add(
                        JobRow(
                            id=job.id,
                            source_path=job.source_path,
                            original_filename=job.original_filename,
                            status=job.status.value,
                            progress=job.progress,
                            output_path=job.output_path,
                            error=job.error,
                            metrics_json=json.dumps(job.metrics),
                            created_at=job.created_at,
                            updated_at=job.updated_at,
                            version=0,
                        )
                    )
                    session.flush()
            except IntegrityError:
                return False
            if job.metadata != JobMetadata():
                session.add(JobMetadataRow(job_id=job.id, **_metadata_values(job.metadata)))
            for kind, models in (
                ("events", events),
                ("track_summaries", track_summaries),
            ):
                session.add_all(
                    PayloadRow(
                        job_id=job.id,
                        kind=kind,
                        position=position,
                        payload_json=item.model_dump_json(),
                    )
                    for position, item in enumerate(models)
                )
        return True

    def _save_models(self, kind: str, job_id: str, models: list[object]) -> None:
        with self._sessions.begin() as session:
            row = _require_job(session, job_id)
            _cas_job(session, row, {}, f"{kind} update")
            session.execute(
                delete(PayloadRow).where(
                    PayloadRow.job_id == job_id,
                    PayloadRow.kind == kind,
                )
            )
            session.add_all(
                PayloadRow(
                    job_id=job_id,
                    kind=kind,
                    position=position,
                    payload_json=item.model_dump_json(),
                )
                for position, item in enumerate(models)
            )

    def _load_json(self, kind: str, job_id: str) -> list[str]:
        self.get(job_id)
        with self._sessions() as session:
            return list(
                session.scalars(
                    select(PayloadRow.payload_json)
                    .where(PayloadRow.job_id == job_id, PayloadRow.kind == kind)
                    .order_by(PayloadRow.position)
                ).all()
            )


class SQLAlchemyStageRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def create(self, job_id: str, stage: StageName) -> StageRecord:
        row = StageRow(
            job_id=job_id,
            stage=stage.value,
            status=StageStatus.PENDING.value,
            attempt=0,
            checkpoint_ms=0,
            version=0,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except IntegrityError:
            return self.get(job_id, stage)
        return _stage_record(row)

    def get(self, job_id: str, stage: StageName) -> StageRecord:
        with self._sessions() as session:
            row = session.get(StageRow, (job_id, stage.value))
            if row is None:
                raise KeyError((job_id, stage.value))
            return _stage_record(row)

    def compare_and_set(
        self,
        job_id: str,
        stage: StageName,
        *,
        expected_status: StageStatus,
        expected_version: int,
        values: dict[str, object],
        expected_lease_owner: str | None = None,
        lease_valid_at: datetime | None = None,
    ) -> StageRecord | None:
        updates = dict(values)
        updates["version"] = expected_version + 1
        predicates = [
            StageRow.job_id == job_id,
            StageRow.stage == stage.value,
            StageRow.status == expected_status.value,
            StageRow.version == expected_version,
        ]
        if lease_valid_at is not None:
            predicates.extend(
                (
                    StageRow.lease_owner == expected_lease_owner,
                    StageRow.lease_expires_at > lease_valid_at,
                )
            )
        with self._sessions.begin() as session:
            result = session.execute(
                update(StageRow)
                .where(*predicates)
                .values(**updates)
            )
            if result.rowcount != 1:
                return None
        return self.get(job_id, stage)


def _job_record(row: JobRow, metadata: JobMetadata | None = None) -> JobRecord:
    return JobRecord(
        id=row.id,
        source_path=row.source_path,
        original_filename=row.original_filename,
        status=JobStatus(row.status),
        progress=row.progress,
        output_path=row.output_path,
        error=row.error,
        metrics=json.loads(row.metrics_json),
        metadata=metadata or JobMetadata(),
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _stage_record(row: StageRow) -> StageRecord:
    return StageRecord(
        job_id=row.job_id,
        stage=StageName(row.stage),
        status=StageStatus(row.status),
        attempt=row.attempt,
        checkpoint_ms=row.checkpoint_ms,
        error=row.error,
        version=row.version,
        lease_owner=row.lease_owner,
        lease_expires_at=_as_utc(row.lease_expires_at) if row.lease_expires_at else None,
        completion_owner=row.completion_owner,
        completion_predecessor_version=row.completion_predecessor_version,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_job(session: Session, job_id: str) -> JobRow:
    row = session.get(JobRow, job_id)
    if row is None:
        raise JobNotFound(job_id)
    return row


def _cas_job(
    session: Session,
    row: JobRow,
    values: dict[str, object],
    action: str,
) -> None:
    updates = dict(values)
    updates["version"] = row.version + 1
    result = session.execute(
        update(JobRow)
        .where(JobRow.id == row.id, JobRow.status == row.status, JobRow.version == row.version)
        .values(**updates)
    )
    if result.rowcount != 1:
        raise InvalidJobTransition(f"concurrent {action} prevented")


def _metadata_record(row: JobMetadataRow | None) -> JobMetadata:
    if row is None:
        return JobMetadata()
    return JobMetadata(
        source=VideoMetadata.model_validate_json(row.source_json) if row.source_json else None,
        output=VideoMetadata.model_validate_json(row.output_json) if row.output_json else None,
        model=ModelMetadata.model_validate_json(row.model_json) if row.model_json else None,
    )


def _metadata_values(metadata: JobMetadata) -> dict[str, str | None]:
    return {
        "source_json": metadata.source.model_dump_json() if metadata.source else None,
        "output_json": metadata.output.model_dump_json() if metadata.output else None,
        "model_json": metadata.model.model_dump_json() if metadata.model else None,
    }
