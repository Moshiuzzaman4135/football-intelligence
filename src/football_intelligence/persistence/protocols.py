"""Persistence contracts shared by the legacy and production repositories."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from football_intelligence.domain import (
    FootballEvent,
    JobMetadata,
    JobRecord,
    JobStatus,
    ModelMetadata,
    StageName,
    StageStatus,
    TrackObservation,
    TrackSummary,
    VideoMetadata,
)
from football_intelligence.persistence.records import StageRecord


@runtime_checkable
class JobStore(Protocol):
    def create(self, source_path: str, original_filename: str) -> JobRecord: ...

    def get(self, job_id: str) -> JobRecord: ...

    def list(self) -> list[JobRecord]: ...

    def transition(
        self,
        job_id: str,
        target: JobStatus,
        *,
        error: str | None = None,
        output_path: str | None = None,
        metrics: dict[str, float | int | str] | None = None,
    ) -> JobRecord: ...

    def update_progress(self, job_id: str, progress: int) -> JobRecord: ...

    def complete_or_stop(
        self,
        job_id: str,
        *,
        output_path: str,
        metrics: dict[str, float | int | str],
    ) -> JobRecord: ...

    def save_events(self, job_id: str, events: list[FootballEvent]) -> None: ...

    def get_events(self, job_id: str) -> list[FootballEvent]: ...

    def save_tracks(self, job_id: str, tracks: list[TrackObservation]) -> None: ...

    def get_tracks(self, job_id: str) -> list[TrackObservation]: ...

    def save_track_summaries(self, job_id: str, summaries: list[TrackSummary]) -> None: ...

    def get_track_summaries(self, job_id: str) -> list[TrackSummary]: ...

    def save_job_metadata(
        self,
        job_id: str,
        *,
        source: VideoMetadata | None = None,
        output: VideoMetadata | None = None,
        model: ModelMetadata | None = None,
    ) -> JobMetadata: ...


class StageStore(Protocol):
    def create(self, job_id: str, stage: StageName) -> StageRecord: ...

    def get(self, job_id: str, stage: StageName) -> StageRecord: ...

    def compare_and_set(
        self,
        job_id: str,
        stage: StageName,
        *,
        expected_status: StageStatus,
        expected_version: int,
        values: dict[str, object],
    ) -> StageRecord | None: ...
