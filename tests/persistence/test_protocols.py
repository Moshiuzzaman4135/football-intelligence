from pathlib import Path

from football_intelligence.persistence import JobStore
from football_intelligence.storage import JobRepository


def test_legacy_sqlite_repository_satisfies_job_store_protocol(tmp_path: Path):
    repository = JobRepository(tmp_path / "legacy.db")

    assert isinstance(repository, JobStore)
