"""Compare-and-set operations for restartable full-match stages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from football_intelligence.domain import StageName, StageStatus
from football_intelligence.persistence.protocols import StageStore
from football_intelligence.persistence.records import StageRecord


class StageConflict(RuntimeError):
    pass


class RetryLimitExceeded(StageConflict):
    pass


def claim_stage(
    store: StageStore,
    job_id: str,
    stage: StageName,
    *,
    worker_id: str,
    now: datetime | None = None,
    lease_seconds: int = 300,
) -> StageRecord:
    current = store.get(job_id, stage)
    claimed_at = _now(now)
    if current.status is StageStatus.COMPLETED:
        return current
    if current.status is StageStatus.RUNNING:
        if current.lease_expires_at and current.lease_expires_at > claimed_at:
            if current.lease_owner == worker_id:
                return current
            raise StageConflict(f"stage is leased by {current.lease_owner}")
    elif current.status is not StageStatus.PENDING:
        raise StageConflict(f"{current.status.value} stage cannot be claimed")
    return _cas_or_conflict(
        store,
        current,
        {
            "status": StageStatus.RUNNING.value,
            "attempt": current.attempt + 1,
            "lease_owner": worker_id,
            "lease_expires_at": claimed_at + timedelta(seconds=lease_seconds),
            "error": None,
        },
    )


def checkpoint_stage(
    store: StageStore,
    job_id: str,
    stage: StageName,
    *,
    checkpoint_ms: int,
    worker_id: str,
    expected_version: int,
    now: datetime | None = None,
    lease_seconds: int = 300,
) -> StageRecord:
    current = store.get(job_id, stage)
    checkpointed_at = _now(now)
    _require_running_owner(current, worker_id, "checkpoint", checkpointed_at)
    if checkpoint_ms <= current.checkpoint_ms:
        raise StageConflict("checkpoint must increase")
    _require_version(current, expected_version)
    return _cas_or_conflict(
        store,
        current,
        {
            "checkpoint_ms": checkpoint_ms,
            "lease_expires_at": checkpointed_at + timedelta(seconds=lease_seconds),
        },
    )


def complete_stage(
    store: StageStore,
    job_id: str,
    stage: StageName,
    *,
    worker_id: str,
    expected_version: int,
    now: datetime | None = None,
) -> StageRecord:
    current = store.get(job_id, stage)
    if current.status is StageStatus.COMPLETED:
        return current
    _require_running_owner(current, worker_id, "completed", _now(now))
    _require_version(current, expected_version)
    return _cas_or_conflict(
        store,
        current,
        {
            "status": StageStatus.COMPLETED.value,
            "lease_owner": None,
            "lease_expires_at": None,
            "error": None,
        },
    )


def fail_stage(
    store: StageStore,
    job_id: str,
    stage: StageName,
    *,
    error: str,
    worker_id: str,
    expected_version: int,
    now: datetime | None = None,
) -> StageRecord:
    current = store.get(job_id, stage)
    _require_running_owner(current, worker_id, "fail", _now(now))
    _require_version(current, expected_version)
    return _cas_or_conflict(
        store,
        current,
        {
            "status": StageStatus.FAILED.value,
            "error": error,
            "lease_owner": None,
            "lease_expires_at": None,
        },
    )


def retry_stage(
    store: StageStore,
    job_id: str,
    stage: StageName,
    *,
    expected_version: int,
    max_attempts: int,
) -> StageRecord:
    current = store.get(job_id, stage)
    if current.status is not StageStatus.FAILED:
        raise StageConflict(f"{current.status.value} stage cannot be retried")
    _require_version(current, expected_version)
    if current.attempt >= max_attempts:
        raise RetryLimitExceeded(f"stage reached attempt limit {max_attempts}")
    return _cas_or_conflict(
        store,
        current,
        {"status": StageStatus.PENDING.value, "error": None},
    )


def _require_running_owner(
    current: StageRecord,
    worker_id: str,
    action: str,
    now: datetime,
) -> None:
    if current.status is not StageStatus.RUNNING:
        raise StageConflict(f"{current.status.value} stage cannot be {action}")
    if current.lease_owner != worker_id:
        raise StageConflict(f"stage is leased by {current.lease_owner}")
    if current.lease_expires_at is None or current.lease_expires_at <= now:
        raise StageConflict("stage lease expired")


def _require_version(current: StageRecord, expected_version: int) -> None:
    if current.version != expected_version:
        raise StageConflict(f"stage version changed from {expected_version} to {current.version}")


def _cas_or_conflict(
    store: StageStore, current: StageRecord, values: dict[str, object]
) -> StageRecord:
    updated = store.compare_and_set(
        current.job_id,
        current.stage,
        expected_status=current.status,
        expected_version=current.version,
        values=values,
    )
    if updated is None:
        raise StageConflict("stage state or version changed concurrently")
    return updated


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(UTC)
