"""Durable repositories and compare-and-set stage operations."""

from football_intelligence.persistence.legacy_import import ImportResult, import_legacy_sqlite
from football_intelligence.persistence.protocols import JobStore, StageStore, UploadStore
from football_intelligence.persistence.records import StageRecord, UploadRecord
from football_intelligence.persistence.sqlalchemy import (
    RawObservationPersistenceError,
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
from football_intelligence.persistence.uploads import (
    InMemoryUploadRepository,
    SQLAlchemyUploadRepository,
    UploadRecordNotFound,
)

__all__ = [
    "JobStore",
    "ImportResult",
    "RawObservationPersistenceError",
    "RetryLimitExceeded",
    "SQLAlchemyJobRepository",
    "SQLAlchemyStageRepository",
    "StageConflict",
    "StageRecord",
    "StageStore",
    "UploadStore",
    "UploadRecord",
    "UploadRecordNotFound",
    "InMemoryUploadRepository",
    "SQLAlchemyUploadRepository",
    "checkpoint_stage",
    "claim_stage",
    "complete_stage",
    "create_persistence_engine",
    "create_schema",
    "fail_stage",
    "import_legacy_sqlite",
    "retry_stage",
]
