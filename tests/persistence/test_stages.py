from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from football_intelligence.domain import StageName, StageStatus
from football_intelligence.persistence import (
    RetryLimitExceeded,
    SQLAlchemyJobRepository,
    SQLAlchemyStageRepository,
    StageConflict,
    checkpoint_stage,
    claim_stage,
    complete_stage,
    create_persistence_engine,
    create_schema,
    fail_stage,
    retry_stage,
)


@pytest.fixture
def stores(tmp_path: Path):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'state.db'}")
    create_schema(engine)
    jobs = SQLAlchemyJobRepository(engine)
    stages = SQLAlchemyStageRepository(engine)
    job = jobs.create("/clips/match.mp4", "match.mp4")
    stages.create(job.id, StageName.OCR)
    return jobs, stages, job


def test_stage_can_be_claimed_checkpointed_and_completed(stores):
    _, stages, job = stores
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    claimed = claim_stage(
        stages,
        job.id,
        StageName.OCR,
        worker_id="worker-a",
        now=now,
        lease_seconds=30,
    )
    checkpointed = checkpoint_stage(
        stages,
        job.id,
        StageName.OCR,
        checkpoint_ms=45_000,
        worker_id="worker-a",
        expected_version=claimed.version,
        now=now + timedelta(seconds=5),
        lease_seconds=30,
    )
    completed = complete_stage(
        stages,
        job.id,
        StageName.OCR,
        worker_id="worker-a",
        expected_version=checkpointed.version,
        now=now + timedelta(seconds=10),
    )

    assert claimed.status is StageStatus.RUNNING
    assert claimed.attempt == 1
    assert claimed.version == 1
    assert checkpointed.checkpoint_ms == 45_000
    assert checkpointed.version == 2
    assert completed.status is StageStatus.COMPLETED
    assert completed.version == 3
    assert completed.lease_owner is None
    assert completed.lease_expires_at is None


def test_illegal_stage_transitions_are_rejected(stores):
    _, stages, job = stores

    with pytest.raises(StageConflict, match="pending stage cannot be completed"):
        complete_stage(
            stages,
            job.id,
            StageName.OCR,
            worker_id="worker-a",
            expected_version=0,
        )
    with pytest.raises(StageConflict, match="pending stage cannot"):
        fail_stage(
            stages,
            job.id,
            StageName.OCR,
            error="OCR runtime unavailable",
            worker_id="worker-a",
            expected_version=0,
        )


def test_completion_is_idempotent_for_replayed_delivery(stores):
    _, stages, job = stores
    claimed = claim_stage(stages, job.id, StageName.OCR, worker_id="worker-a")

    first = complete_stage(
        stages,
        job.id,
        StageName.OCR,
        worker_id="worker-a",
        expected_version=claimed.version,
    )
    replay = complete_stage(
        stages,
        job.id,
        StageName.OCR,
        worker_id="worker-a",
        expected_version=claimed.version,
    )

    assert replay == first
    assert stages.get(job.id, StageName.OCR).version == first.version


def test_checkpoint_must_increase_and_use_current_version(stores):
    _, stages, job = stores
    claimed = claim_stage(stages, job.id, StageName.OCR, worker_id="worker-a")
    checkpointed = checkpoint_stage(
        stages,
        job.id,
        StageName.OCR,
        checkpoint_ms=45_000,
        worker_id="worker-a",
        expected_version=claimed.version,
    )

    with pytest.raises(StageConflict, match="checkpoint must increase"):
        checkpoint_stage(
            stages,
            job.id,
            StageName.OCR,
            checkpoint_ms=44_999,
            worker_id="worker-a",
            expected_version=checkpointed.version,
        )
    with pytest.raises(StageConflict, match="version changed"):
        checkpoint_stage(
            stages,
            job.id,
            StageName.OCR,
            checkpoint_ms=60_000,
            worker_id="worker-a",
            expected_version=claimed.version,
        )


def test_expired_lease_can_be_reclaimed_but_active_lease_suppresses_duplicate(stores):
    _, stages, job = stores
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    claimed = claim_stage(
        stages,
        job.id,
        StageName.OCR,
        worker_id="worker-a",
        now=now,
        lease_seconds=30,
    )

    with pytest.raises(StageConflict, match="leased by worker-a"):
        claim_stage(
            stages,
            job.id,
            StageName.OCR,
            worker_id="worker-b",
            now=now + timedelta(seconds=29),
            lease_seconds=30,
        )

    reclaimed = claim_stage(
        stages,
        job.id,
        StageName.OCR,
        worker_id="worker-b",
        now=now + timedelta(seconds=30),
        lease_seconds=30,
    )

    assert reclaimed.attempt == 2
    assert reclaimed.version == claimed.version + 1
    assert reclaimed.lease_owner == "worker-b"


def test_expired_lease_cannot_checkpoint_or_complete(stores):
    _, stages, job = stores
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    claimed = claim_stage(
        stages,
        job.id,
        StageName.OCR,
        worker_id="worker-a",
        now=now,
        lease_seconds=30,
    )

    with pytest.raises(StageConflict, match="lease expired"):
        checkpoint_stage(
            stages,
            job.id,
            StageName.OCR,
            checkpoint_ms=45_000,
            worker_id="worker-a",
            expected_version=claimed.version,
            now=now + timedelta(seconds=30),
        )
    with pytest.raises(StageConflict, match="lease expired"):
        complete_stage(
            stages,
            job.id,
            StageName.OCR,
            worker_id="worker-a",
            expected_version=claimed.version,
            now=now + timedelta(seconds=30),
        )


def test_failed_stage_can_retry_only_below_attempt_limit(stores):
    _, stages, job = stores
    first = claim_stage(stages, job.id, StageName.OCR, worker_id="worker-a")
    failed = fail_stage(
        stages,
        job.id,
        StageName.OCR,
        error="temporary OCR failure",
        worker_id="worker-a",
        expected_version=first.version,
    )
    pending = retry_stage(
        stages,
        job.id,
        StageName.OCR,
        expected_version=failed.version,
        max_attempts=2,
    )
    second = claim_stage(stages, job.id, StageName.OCR, worker_id="worker-b")
    failed_again = fail_stage(
        stages,
        job.id,
        StageName.OCR,
        error="permanent OCR failure",
        worker_id="worker-b",
        expected_version=second.version,
    )

    assert pending.status is StageStatus.PENDING
    assert pending.error is None
    with pytest.raises(RetryLimitExceeded, match="attempt limit 2"):
        retry_stage(
            stages,
            job.id,
            StageName.OCR,
            expected_version=failed_again.version,
            max_attempts=2,
        )


def test_concurrent_claims_have_one_winner(stores):
    _, stages, job = stores

    def try_claim(worker_id: str):
        try:
            return claim_stage(stages, job.id, StageName.OCR, worker_id=worker_id)
        except StageConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(try_claim, ["worker-a", "worker-b"]))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0].attempt == 1
    assert stages.get(job.id, StageName.OCR).lease_owner in {"worker-a", "worker-b"}
