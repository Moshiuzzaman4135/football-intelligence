"""Typed records returned by durable persistence stores."""

from datetime import datetime

from pydantic import BaseModel, Field

from football_intelligence.domain import StageName, StageStatus, UploadPart, UploadStatus


class StageRecord(BaseModel):
    job_id: str = Field(min_length=1)
    stage: StageName
    status: StageStatus
    attempt: int = Field(ge=0)
    checkpoint_ms: int = Field(ge=0)
    error: str | None
    version: int = Field(ge=0)
    lease_owner: str | None
    lease_expires_at: datetime | None
    completion_owner: str | None
    completion_predecessor_version: int | None = Field(default=None, ge=0)


class UploadRecord(BaseModel):
    id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    storage_upload_id: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    original_filename: str = Field(min_length=1)
    size_bytes: int = Field(gt=0, le=12 * 1024**3)
    part_size_bytes: int = Field(gt=0)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    status: UploadStatus
    planned_job_id: str = Field(min_length=1)
    completion_parts: list[UploadPart]
    validated_parts: list[UploadPart]
    object_size_bytes: int | None = Field(default=None, ge=0)
    object_checksum_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    object_etag: str | None = None
    job_id: str | None = None
    cleanup_completed_at: datetime | None = None
    version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
