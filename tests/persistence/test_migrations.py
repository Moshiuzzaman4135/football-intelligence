import logging
from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from football_intelligence.persistence import create_persistence_engine

PROJECT_ROOT = Path(__file__).parents[2]


def test_initial_migration_upgrades_downgrades_and_reupgrades(tmp_path: Path):
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


def test_running_migrations_does_not_disable_application_loggers(tmp_path: Path):
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'logging.db'}")
    application_logger = logging.getLogger("football_intelligence.pipeline")
    application_logger.disabled = False

    command.upgrade(config, "head")

    assert application_logger.disabled is False
