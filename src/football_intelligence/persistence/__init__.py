"""Durable repositories and compare-and-set stage operations."""

from football_intelligence.persistence.legacy_import import ImportResult, import_legacy_sqlite
from football_intelligence.persistence.protocols import JobStore, StageStore
from football_intelligence.persistence.records import StageRecord
from football_intelligence.persistence.sqlalchemy import (
    SQLAlchemyJobRepository,
    SQLAlchemyStageRepository,
    create_persistence_engine,
    create_schema,
)
from football_intelligence.persistence.stages import (
    RetryLimitExceeded,
    StageConflict,
    checkpoint_stage,
    claim_stage,
    complete_stage,
    fail_stage,
    retry_stage,
)

__all__ = [
    "JobStore",
    "ImportResult",
    "RetryLimitExceeded",
    "SQLAlchemyJobRepository",
    "SQLAlchemyStageRepository",
    "StageConflict",
    "StageRecord",
    "StageStore",
    "checkpoint_stage",
    "claim_stage",
    "complete_stage",
    "create_persistence_engine",
    "create_schema",
    "fail_stage",
    "import_legacy_sqlite",
    "retry_stage",
]
