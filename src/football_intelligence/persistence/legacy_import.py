"""Read-only import from the original sqlite3 repository schema."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from football_intelligence.domain import (
    FootballEvent,
    JobMetadata,
    JobRecord,
    JobStatus,
    ModelMetadata,
    TrackSummary,
    VideoMetadata,
)
from football_intelligence.persistence.sqlalchemy import SQLAlchemyJobRepository


@dataclass(frozen=True)
class ImportResult:
    imported_jobs: int
    skipped_jobs: int
    skipped_track_observations: int


def import_legacy_sqlite(
    database_path: str | Path, target: SQLAlchemyJobRepository
) -> ImportResult:
    """Copy legacy rows without opening the source database for writes."""
    source = Path(database_path).resolve()
    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    imported = 0
    skipped = 0
    skipped_track_observations = 0
    try:
        connection.execute("BEGIN")
        jobs = connection.execute("SELECT * FROM jobs ORDER BY created_at, id").fetchall()
        for row in jobs:
            job_id = row["id"]
            metadata = _read_metadata(connection, job_id)
            job = JobRecord(
                id=job_id,
                source_path=row["source_path"],
                original_filename=row["original_filename"],
                status=JobStatus(row["status"]),
                progress=row["progress"],
                output_path=row["output_path"],
                error=row["error"],
                metrics=json.loads(row["metrics_json"]),
                metadata=metadata,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            inserted = target.import_job(
                job,
                events=_read_models(connection, "events", job_id, FootballEvent),
                track_summaries=_read_models(connection, "track_summaries", job_id, TrackSummary),
            )
            skipped_track_observations += connection.execute(
                "SELECT COUNT(*) FROM tracks WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
            imported += int(inserted)
            skipped += int(not inserted)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return ImportResult(
        imported_jobs=imported,
        skipped_jobs=skipped,
        skipped_track_observations=skipped_track_observations,
    )


def _read_models(connection, table: str, job_id: str, model_type):
    rows = connection.execute(
        f"SELECT payload_json FROM {table} WHERE job_id = ? ORDER BY position",
        (job_id,),
    ).fetchall()
    return [model_type.model_validate_json(row["payload_json"]) for row in rows]


def _read_metadata(connection: sqlite3.Connection, job_id: str) -> JobMetadata:
    row = connection.execute(
        "SELECT source_json, output_json, model_json FROM job_metadata WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return JobMetadata()
    return JobMetadata(
        source=VideoMetadata.model_validate_json(row["source_json"])
        if row["source_json"]
        else None,
        output=VideoMetadata.model_validate_json(row["output_json"])
        if row["output_json"]
        else None,
        model=ModelMetadata.model_validate_json(row["model_json"]) if row["model_json"] else None,
    )
