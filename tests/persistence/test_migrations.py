import logging
from io import StringIO
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from football_intelligence.domain import StageName, StageStatus
from football_intelligence.persistence import (
    SQLAlchemyJobRepository,
    SQLAlchemyStageRepository,
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
    }.issubset(inspect(engine).get_table_names())

    command.downgrade(config, "base")
    assert not {
        "jobs",
        "job_stages",
        "job_payloads",
        "job_metadata",
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
