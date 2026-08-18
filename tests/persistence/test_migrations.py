import logging
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from football_intelligence.domain import StageName, StageStatus, UploadStatus
from football_intelligence.persistence import (
    SQLAlchemyJobRepository,
    SQLAlchemyStageRepository,
    SQLAlchemyUploadRepository,
    UploadRecord,
    claim_stage,
    complete_stage,
    create_persistence_engine,
)

PROJECT_ROOT = Path(__file__).parents[2]


def test_initial_migration_upgrades_downgrades_and_reupgrades(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("FOOTBALL_DATABASE_URL", raising=False)
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_persistence_engine(database_url)

    command.upgrade(config, "head")
    assert {
        "jobs",
        "job_stages",
        "job_payloads",
        "job_metadata",
        "upload_sessions",
    }.issubset(inspect(engine).get_table_names())

    command.downgrade(config, "base")
    assert not {
        "jobs",
        "job_stages",
        "job_payloads",
        "job_metadata",
        "upload_sessions",
    }.intersection(inspect(engine).get_table_names())

    command.upgrade(config, "head")
    assert "job_stages" in inspect(engine).get_table_names()


def test_stamped_initial_revision_receives_completion_identity_on_head_upgrade(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("FOOTBALL_DATABASE_URL", raising=False)
    database_url = f"sqlite:///{tmp_path / 'existing.db'}"
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_persistence_engine(database_url)
    completion_columns = {"completion_owner", "completion_predecessor_version"}

    command.upgrade(config, "20260818_01")
    initial_columns = {
        column["name"] for column in inspect(engine).get_columns("job_stages")
    }
    assert completion_columns.isdisjoint(initial_columns)

    command.upgrade(config, "head")
    head_columns = {column["name"] for column in inspect(engine).get_columns("job_stages")}
    assert completion_columns.issubset(head_columns)

    jobs = SQLAlchemyJobRepository(engine)
    stages = SQLAlchemyStageRepository(engine)
    job = jobs.create("/clips/match.mp4", "match.mp4")
    stages.create(job.id, StageName.OCR)
    claimed = claim_stage(stages, job.id, StageName.OCR, worker_id="worker-a")
    completed = complete_stage(
        stages,
        job.id,
        StageName.OCR,
        worker_id="worker-a",
        expected_version=claimed.version,
    )
    assert completed.status is StageStatus.COMPLETED
    assert completed.completion_owner == "worker-a"

    command.downgrade(config, "20260818_01")
    downgraded_columns = {
        column["name"] for column in inspect(engine).get_columns("job_stages")
    }
    assert completion_columns.isdisjoint(downgraded_columns)


def test_existing_task2_database_receives_reversible_upload_sessions(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("FOOTBALL_DATABASE_URL", raising=False)
    database_url = f"sqlite:///{tmp_path / 'task2.db'}"
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_persistence_engine(database_url)

    command.upgrade(config, "20260818_02")
    assert "upload_sessions" not in inspect(engine).get_table_names()

    command.upgrade(config, "head")
    columns = {
        column["name"] for column in inspect(engine).get_columns("upload_sessions")
    }
    assert {
        "id",
        "owner_id",
        "storage_upload_id",
        "object_key",
        "status",
        "completion_parts_json",
        "validated_parts_json",
        "planned_job_id",
        "job_id",
        "cleanup_completed_at",
        "version",
    }.issubset(columns)

    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    upload = UploadRecord(
        id="upload-after-migration",
        owner_id="operator-1",
        storage_upload_id="private-s3-id",
        object_key="uploads/upload-after-migration/source.mp4",
        original_filename="match.mp4",
        size_bytes=4,
        part_size_bytes=16 * 1024 * 1024,
        checksum_sha256="a" * 64,
        expires_at=now + timedelta(hours=24),
        status=UploadStatus.ACTIVE,
        planned_job_id="upload-after-migration",
        completion_parts=[],
        validated_parts=[],
        version=0,
        created_at=now,
        updated_at=now,
    )
    uploads = SQLAlchemyUploadRepository(engine)
    assert uploads.create(upload) == upload

    command.downgrade(config, "20260818_02")
    assert "upload_sessions" not in inspect(engine).get_table_names()


def test_running_migrations_does_not_disable_application_loggers(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FOOTBALL_DATABASE_URL", raising=False)
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'logging.db'}")
    application_logger = logging.getLogger("football_intelligence.pipeline")
    application_logger.disabled = False

    command.upgrade(config, "head")

    assert application_logger.disabled is False


def test_normal_migration_requires_database_url(monkeypatch):
    monkeypatch.delenv("FOOTBALL_DATABASE_URL", raising=False)
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))

    with pytest.raises(RuntimeError, match="FOOTBALL_DATABASE_URL is required"):
        command.upgrade(config, "head")


def test_offline_migration_renders_postgresql_ddl(monkeypatch):
    monkeypatch.setenv(
        "FOOTBALL_DATABASE_URL", "postgresql+psycopg://user:pass@database/football"
    )
    output = StringIO()
    config = Config(PROJECT_ROOT / "alembic.ini", output_buffer=output)
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))

    command.upgrade(config, "head", sql=True)

    ddl = output.getvalue()
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "CREATE TABLE job_stages" in ddl
