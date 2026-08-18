"""SQLite metadata repository with explicit job lifecycle transitions."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from football_intelligence.domain import FootballEvent, JobRecord, JobStatus, TrackObservation


class JobNotFound(KeyError):
    pass


class InvalidJobTransition(ValueError):
    pass


ALLOWED_TRANSITIONS = {
    JobStatus.CREATED: {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.STOPPED},
    JobStatus.RUNNING: {
        JobStatus.STOPPING,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.STOPPED,
    },
    JobStatus.STOPPING: {JobStatus.STOPPED, JobStatus.FAILED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
    JobStatus.STOPPED: set(),
}


class JobRepository:
    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    output_path TEXT,
                    error TEXT,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    job_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (job_id, position)
                );
                CREATE TABLE IF NOT EXISTS tracks (
                    job_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (job_id, position)
                );
                """
            )

    def create(self, source_path: str, original_filename: str) -> JobRecord:
        now = datetime.now(UTC)
        job = JobRecord(
            id=str(uuid4()),
            source_path=source_path,
            original_filename=original_filename,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO jobs
                (id, source_path, original_filename, status, progress, output_path, error,
                 metrics_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._job_values(job),
            )
        return job

    def get(self, job_id: str) -> JobRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return self._row_to_job(row)

    def list(self) -> list[JobRecord]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._row_to_job(row) for row in rows]

    def transition(
        self,
        job_id: str,
        target: JobStatus,
        *,
        error: str | None = None,
        output_path: str | None = None,
        metrics: dict[str, float | int | str] | None = None,
    ) -> JobRecord:
        current = self.get(job_id)
        if target not in ALLOWED_TRANSITIONS[current.status]:
            raise InvalidJobTransition(f"{current.status.value} -> {target.value}")
        progress = 100 if target is JobStatus.COMPLETED else current.progress
        updated = current.model_copy(
            update={
                "status": target,
                "progress": progress,
                "error": error if error is not None else current.error,
                "output_path": output_path if output_path is not None else current.output_path,
                "metrics": metrics if metrics is not None else current.metrics,
                "updated_at": datetime.now(UTC),
            }
        )
        self._replace_job(updated)
        return updated

    def update_progress(self, job_id: str, progress: int) -> JobRecord:
        if not 0 <= progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        current = self.get(job_id)
        if progress < current.progress:
            raise ValueError("progress cannot decrease")
        updated = current.model_copy(update={"progress": progress, "updated_at": datetime.now(UTC)})
        self._replace_job(updated)
        return updated

    def save_events(self, job_id: str, events: list[FootballEvent]) -> None:
        self._save_models("events", job_id, events)

    def get_events(self, job_id: str) -> list[FootballEvent]:
        return [
            FootballEvent.model_validate_json(item) for item in self._load_json("events", job_id)
        ]

    def save_tracks(self, job_id: str, tracks: list[TrackObservation]) -> None:
        self._save_models("tracks", job_id, tracks)

    def get_tracks(self, job_id: str) -> list[TrackObservation]:
        return [
            TrackObservation.model_validate_json(item) for item in self._load_json("tracks", job_id)
        ]

    def _save_models(self, table: str, job_id: str, models: list[object]) -> None:
        self.get(job_id)
        with self._connect() as connection:
            connection.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
            connection.executemany(
                f"INSERT INTO {table} (job_id, position, payload_json) VALUES (?, ?, ?)",
                [(job_id, index, model.model_dump_json()) for index, model in enumerate(models)],
            )

    def _load_json(self, table: str, job_id: str) -> list[str]:
        self.get(job_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM {table} WHERE job_id = ? ORDER BY position",
                (job_id,),
            ).fetchall()
        return [row["payload_json"] for row in rows]

    def _replace_job(self, job: JobRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE jobs SET source_path=?, original_filename=?, status=?, progress=?,
                output_path=?, error=?, metrics_json=?, created_at=?, updated_at=? WHERE id=?""",
                (*self._job_values(job)[1:], job.id),
            )

    @staticmethod
    def _job_values(job: JobRecord) -> tuple[object, ...]:
        return (
            job.id,
            job.source_path,
            job.original_filename,
            job.status.value,
            job.progress,
            job.output_path,
            job.error,
            json.dumps(job.metrics),
            job.created_at.isoformat(),
            job.updated_at.isoformat(),
        )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            source_path=row["source_path"],
            original_filename=row["original_filename"],
            status=JobStatus(row["status"]),
            progress=row["progress"],
            output_path=row["output_path"],
            error=row["error"],
            metrics=json.loads(row["metrics_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
