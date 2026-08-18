"""Atomic restart manifest for the single-host full-match runner."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import cv2
from pydantic import BaseModel, Field, computed_field

from football_intelligence.domain import FootballEvent, ScoreboardObservation, ScoreboardRegion
from football_intelligence.fullmatch.chunks import plan_chunks
from football_intelligence.fullmatch.media import MediaProbe, atomic_json_write
from football_intelligence.fullmatch.ocr import ConsensusState


class RuntimeProvenance(BaseModel, frozen=True):
    detector: str
    detector_model: str
    detector_device: str
    detector_framework: str
    detector_version: str
    detector_config: dict[str, str] = Field(default_factory=dict)
    detector_model_sha256: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    detector_adapter_sha256: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    tracker: str
    tracker_config: dict[str, str]
    tracker_adapter_sha256: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    ocr_engine: str
    ocr_model: str
    ocr_version: str
    ocr_model_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ocr_adapter_sha256: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")


class RunnerOptions(BaseModel, frozen=True):
    chunk_ms: int = Field(default=120_000, gt=0)
    overlap_ms: int = Field(default=5_000, ge=0)
    ocr_interval_ms: int = Field(default=1_000, ge=1_000)
    scoreboard_region: ScoreboardRegion = Field(
        default_factory=lambda: ScoreboardRegion(x=0, y=0, width=1, height=0.2)
    )
    provenance: RuntimeProvenance = Field(
        default_factory=lambda: RuntimeProvenance(
            detector="football_intelligence.detection.color.ColorDetector",
            detector_model="deterministic-color",
            detector_device="cpu",
            detector_framework="opencv",
            detector_version=cv2.__version__,
            detector_config={"confidence": "0.25"},
            tracker="football_intelligence.tracking.iou.IoUTracker",
            tracker_config={"iou_threshold": "0.25", "max_missed": "8"},
            ocr_engine="football_intelligence.fullmatch.ocr.FakeOcrEngine",
            ocr_model="finite-test-results",
            ocr_version="1",
            ocr_model_sha256="0" * 64,
        )
    )
    max_frame_errors: int = Field(default=10, ge=0)
    output_video_codec: Literal["h264"] = "h264"
    output_pixel_format: Literal["yuv420p"] = "yuv420p"
    output_audio_codec: Literal["aac"] = "aac"
    encoder_preset: Literal["veryfast"] = "veryfast"
    proxy_max_width: Literal[1280] = 1280
    proxy_max_height: Literal[720] = 720
    proxy_max_fps: Literal[25] = 25
    event_post_window_ms: int = Field(default=1_000, ge=0)
    trail_max_points: int = Field(default=30, gt=0)
    trail_max_inactive_frames: int = Field(default=30, ge=0)
    trail_max_tracks: int = Field(default=512, gt=0)
    heat_map_columns: Literal[32] = 32
    heat_map_rows: Literal[18] = 18


class RawOcrEvidence(BaseModel):
    timestamp_ms: int = Field(ge=0)
    frame_index: int = Field(ge=0)
    raw_text: str
    raw_confidence: float = Field(ge=0, le=1)


class ChunkRecord(BaseModel):
    index: int = Field(ge=0)
    context_start_ms: int = Field(ge=0)
    output_start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    status: Literal["pending", "running", "completed"] = "pending"
    output_path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    events: list[FootballEvent] = Field(default_factory=list)
    scoreboard: list[ScoreboardObservation] = Field(default_factory=list)
    raw_ocr_evidence: list[RawOcrEvidence] = Field(default_factory=list)
    consensus_state: ConsensusState | None = None
    heat_map_counts: list[list[int]] | None = None
    peak_observations: int = Field(default=0, ge=0)
    completed_at: datetime | None = None


class FinalArtifact(BaseModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    heat_map_path: str
    heat_map_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    probe: MediaProbe | None = None
    completed_at: datetime


class FullMatchManifest(BaseModel):
    schema_version: Literal[2] = 2
    job_id: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    options: RunnerOptions
    source: MediaProbe
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proxy: MediaProbe
    proxy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunks: list[ChunkRecord]
    prepared_final_artifact: FinalArtifact | None = None
    final_artifact: FinalArtifact | None = None
    peak_observations: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def progress(self) -> int:
        if self.final_artifact is not None:
            return 100
        if not self.chunks:
            return 0
        complete = sum(chunk.status == "completed" for chunk in self.chunks)
        return min(99, complete * 95 // len(self.chunks))

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        source_uri: str,
        options: RunnerOptions,
        source: MediaProbe,
        proxy: MediaProbe,
        source_sha256: str = "0" * 64,
        proxy_sha256: str = "0" * 64,
    ) -> FullMatchManifest:
        windows = plan_chunks(proxy.duration_ms, options.chunk_ms, options.overlap_ms)
        chunks = [
            ChunkRecord(
                index=index,
                context_start_ms=start,
                output_start_ms=0 if index == 0 else windows[index - 1][1],
                end_ms=end,
            )
            for index, (start, end) in enumerate(windows)
        ]
        now = datetime.now(UTC)
        return cls(
            job_id=job_id,
            source_uri=source_uri,
            options=options,
            source=source,
            source_sha256=source_sha256,
            proxy=proxy,
            proxy_sha256=proxy_sha256,
            chunks=chunks,
            created_at=now,
            updated_at=now,
        )


class ManifestStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> FullMatchManifest:
        return FullMatchManifest.model_validate(self.load_payload())

    def load_payload(self) -> dict[str, object]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest root must be an object")
        return payload

    def save(self, manifest: FullMatchManifest) -> None:
        updated = manifest.model_copy(update={"updated_at": datetime.now(UTC)})
        atomic_json_write(
            self.path,
            updated.model_dump(mode="json", exclude={"progress"}),
        )
        manifest.updated_at = updated.updated_at
