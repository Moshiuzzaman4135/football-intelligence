"""Normalized, vendor-independent football intelligence schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, model_validator

TeamId = Literal["team_1", "team_2", "unknown"]
TrackState = Literal["tentative", "confirmed", "lost"]


class JobStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class StageName(StrEnum):
    UPLOAD_VALIDATION = "upload_validation"
    SOURCE_PROBE_PROXY = "source_probe_proxy"
    SHOT_CLASSIFICATION = "shot_classification"
    OCR = "ocr"
    DETECTION_TRACKING = "detection_tracking"
    TEAM_CALIBRATION = "team_calibration"
    ACTION_SPOTTING = "action_spotting"
    EVENT_FUSION = "event_fusion"
    HEAT_MAPS = "heat_maps"
    CLIPS = "clips"
    ANNOTATED_RENDERING = "annotated_rendering"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class UploadStatus(StrEnum):
    ACTIVE = "active"
    COMPLETING = "completing"
    VALIDATED = "validated"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    EXPIRED = "expired"


class EventStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


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
    state: TrackState = "confirmed"
    hits: int = Field(default=0, ge=0)


class EventEvidence(BaseModel):
    kind: str = Field(min_length=1)
    value: Any
    confidence: float = Field(ge=0, le=1)
    frame_refs: list[int] = Field(default_factory=list)
    detail: str | None = None


class JobStage(BaseModel):
    job_id: str = Field(min_length=1)
    stage: StageName
    status: StageStatus = StageStatus.PENDING
    attempt: int = Field(default=0, ge=0)
    checkpoint_ms: int = Field(default=0, ge=0)
    error: str | None = None

    def transition_to(self, status: StageStatus) -> "JobStage":
        allowed_transitions = {
            StageStatus.PENDING: {StageStatus.RUNNING, StageStatus.STOPPED},
            StageStatus.RUNNING: {
                StageStatus.COMPLETED,
                StageStatus.FAILED,
                StageStatus.STOPPED,
            },
            StageStatus.FAILED: {StageStatus.PENDING},
            StageStatus.COMPLETED: set(),
            StageStatus.STOPPED: set(),
        }
        if status is not self.status and status not in allowed_transitions[self.status]:
            raise ValueError(f"cannot transition stage from {self.status} to {status}")
        return self.model_copy(update={"status": status})


class ScoreboardRegion(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "ScoreboardRegion":
        if self.x + self.width > 1:
            raise ValueError("scoreboard region must fit within the normalized frame")
        if self.y + self.height > 1:
            raise ValueError("scoreboard region must fit within the normalized frame")
        return self


class ScoreboardObservation(BaseModel):
    timestamp_ms: int = Field(ge=0)
    match_clock_ms: int = Field(ge=0)
    period: int = Field(ge=1)
    home_team: str = Field(min_length=1)
    away_team: str = Field(min_length=1)
    home_score: int = Field(ge=0)
    away_score: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    region: ScoreboardRegion
    frame_index: int = Field(ge=0)


class CalibrationObservation(BaseModel):
    timestamp_ms: int = Field(ge=0)
    frame_index: int = Field(ge=0)
    homography: list[float] = Field(min_length=9, max_length=9)
    reprojection_error_m: float = Field(ge=0)


class Artifact(BaseModel):
    job_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class UploadPart(BaseModel):
    part_number: int = Field(ge=1)
    size_bytes: int = Field(ge=0)
    etag: str = Field(min_length=1)
    checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class UploadSession(BaseModel):
    id: str = Field(min_length=1)
    job_id: str | None = Field(default=None, min_length=1)
    object_key: str = Field(min_length=1)
    original_filename: str = Field(min_length=1)
    size_bytes: int = Field(gt=0, le=12 * 1024**3)
    part_size_bytes: Literal[16 * 1024 * 1024]
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime | None = None
    status: UploadStatus = UploadStatus.ACTIVE
    version: int = Field(default=0, ge=0)
    uploaded_parts: list[UploadPart] = Field(default_factory=list)


class EventReview(BaseModel):
    event_id: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    decision: Literal[EventStatus.CONFIRMED, EventStatus.REJECTED]
    note: str = Field(min_length=1)
    reviewed_at: datetime


class ModelManifest(BaseModel):
    model_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source: str = Field(min_length=1)
    license: str = Field(min_length=1)
    weight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    classes: list[str] = Field(min_length=1)
    runtime: str = Field(min_length=1)
    device: str = Field(min_length=1)
    benchmark: str = Field(min_length=1)
    limitations: str = Field(min_length=1)


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
    status: EventStatus = EventStatus.CANDIDATE
    period: int | None = Field(default=None, ge=1)
    match_clock_ms: int | None = Field(default=None, ge=0)
    score_transition: str | None = None
    producer_version: str | None = None
    review: EventReview | None = None
    original_model_output: dict[str, Any] | None = None

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


class FullMatchVideoMetadata(VideoMetadata):
    duration_ms: int = Field(gt=0, le=150 * 60 * 1000)


class ProxyVideoMetadata(VideoMetadata):
    height: int = Field(gt=0, le=1080)
    fps: float = Field(gt=0, le=25)


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
