"""Normalized, vendor-independent football intelligence schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, model_validator

TeamId = Literal["team_1", "team_2", "unknown"]


class JobStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class BoundingBox(BaseModel):
    x1: float = Field(ge=0)
    y1: float = Field(ge=0)
    x2: float = Field(gt=0)
    y2: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_area(self) -> "BoundingBox":
        if self.x2 <= self.x1:
            raise ValueError("x2 must be greater than x1")
        if self.y2 <= self.y1:
            raise ValueError("y2 must be greater than y1")
        return self

    @computed_field
    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @computed_field
    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @computed_field
    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.width / 2, self.y1 + self.height / 2)


class Detection(BaseModel):
    object_class: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    bbox: BoundingBox
    frame_index: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)


class TrackObservation(BaseModel):
    track_id: int = Field(ge=0)
    object_class: str = Field(min_length=1)
    bbox: BoundingBox
    confidence: float = Field(ge=0, le=1)
    timestamp_ms: int = Field(ge=0)
    frame_index: int = Field(ge=0)
    team_id: TeamId = "unknown"
    player: str = "unknown"
    pitch_x: float | None = None
    pitch_y: float | None = None


class EventEvidence(BaseModel):
    kind: str = Field(min_length=1)
    value: Any
    confidence: float = Field(ge=0, le=1)
    frame_refs: list[int] = Field(default_factory=list)
    detail: str | None = None


class FootballEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    job_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    game_time: str | None = None
    team: TeamId = "unknown"
    player: str = "unknown"
    description: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence: list[EventEvidence] = Field(min_length=1)
    source: list[str] = Field(min_length=1)
    track_ids: list[int] = Field(default_factory=list)
    frame_refs: list[int] = Field(default_factory=list)
    needs_review: bool = True

    @model_validator(mode="after")
    def validate_time_range(self) -> "FootballEvent":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        return self


class VideoMetadata(BaseModel):
    source_path: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    frame_count: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    codec: str


class ModelMetadata(BaseModel):
    detector: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    device: str = Field(min_length=1)
    framework: str = Field(min_length=1)
    weight_sha256: str | None = None


class JobMetadata(BaseModel):
    source: VideoMetadata | None = None
    output: VideoMetadata | None = None
    model: ModelMetadata | None = None


class TrackSummary(BaseModel):
    track_id: int = Field(ge=0)
    object_class: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    first_frame: int = Field(ge=0)
    last_frame: int = Field(ge=0)
    observation_count: int = Field(gt=0)
    mean_confidence: float = Field(ge=0, le=1)
    max_confidence: float = Field(ge=0, le=1)
    team_id: TeamId = "unknown"


class JobRecord(BaseModel):
    id: str
    source_path: str
    original_filename: str
    status: JobStatus = JobStatus.CREATED
    progress: int = Field(default=0, ge=0, le=100)
    output_path: str | None = None
    error: str | None = None
    metrics: dict[str, float | int | str] = Field(default_factory=dict)
    metadata: JobMetadata = Field(default_factory=JobMetadata)
    created_at: datetime
    updated_at: datetime
