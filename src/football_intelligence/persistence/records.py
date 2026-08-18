"""Typed records returned by durable persistence stores."""

from datetime import datetime

from pydantic import BaseModel, Field

from football_intelligence.domain import StageName, StageStatus


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
