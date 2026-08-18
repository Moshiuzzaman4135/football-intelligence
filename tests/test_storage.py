from pathlib import Path

import pytest

from football_intelligence.domain import (
    BoundingBox,
    EventEvidence,
    FootballEvent,
    JobStatus,
    ModelMetadata,
    TrackObservation,
    TrackSummary,
    VideoMetadata,
)
from football_intelligence.storage import InvalidJobTransition, JobRepository


@pytest.fixture
def repository(tmp_path: Path) -> JobRepository:
    return JobRepository(tmp_path / "jobs.db")


def test_job_creation_has_safe_defaults(repository):
    job = repository.create("/clips/match.mp4", "match.mp4")

    assert job.status is JobStatus.CREATED
    assert job.progress == 0
    assert job.source_path == "/clips/match.mp4"
    assert repository.get(job.id) == job


def test_create_with_id_is_idempotent_for_upload_delivery(repository):
    first = repository.create_with_id("upload-1", "/objects/source.mp4", "match.mp4")
    replay = repository.create_with_id("upload-1", "/objects/source.mp4", "match.mp4")

    assert first == replay
    assert first.id == "upload-1"
    assert repository.list() == [first]


def test_job_state_machine_allows_normal_completion(repository):
    job = repository.create("/clips/match.mp4", "match.mp4")

    running = repository.transition(job.id, JobStatus.RUNNING)
    completed = repository.transition(job.id, JobStatus.COMPLETED)

    assert running.status is JobStatus.RUNNING
    assert completed.status is JobStatus.COMPLETED
    assert completed.progress == 100


def test_atomic_completion_honors_a_winning_stop_request(repository):
    job = repository.create("/clips/match.mp4", "match.mp4")
    repository.transition(job.id, JobStatus.RUNNING)
    repository.transition(job.id, JobStatus.STOPPING)

    stopped = repository.complete_or_stop(
        job.id, output_path="/outputs/match.mp4", metrics={"frames": 10}
    )

    assert stopped.status is JobStatus.STOPPED
    assert stopped.progress < 100
    assert stopped.output_path is None


def test_terminal_job_cannot_be_restarted(repository):
    job = repository.create("/clips/match.mp4", "match.mp4")
    repository.transition(job.id, JobStatus.RUNNING)
    repository.transition(job.id, JobStatus.COMPLETED)

    with pytest.raises(InvalidJobTransition, match="completed -> running"):
        repository.transition(job.id, JobStatus.RUNNING)


def test_progress_is_monotonic_and_capped(repository):
    job = repository.create("/clips/match.mp4", "match.mp4")
    repository.transition(job.id, JobStatus.RUNNING)
    repository.update_progress(job.id, 45)

    with pytest.raises(ValueError, match="progress cannot decrease"):
        repository.update_progress(job.id, 44)
    with pytest.raises(ValueError, match="between 0 and 100"):
        repository.update_progress(job.id, 101)


def test_events_and_tracks_round_trip_as_normalized_models(repository):
    job = repository.create("/clips/match.mp4", "match.mp4")
    event = FootballEvent(
        job_id=job.id,
        event_type="kick_candidate",
        start_ms=100,
        end_ms=300,
        description="Ball accelerated near a player",
        confidence=0.72,
        evidence=[EventEvidence(kind="speed", value=8.0, confidence=0.72)],
        source=["heuristic.temporal"],
    )
    track = TrackObservation(
        track_id=2,
        object_class="ball",
        bbox=BoundingBox(x1=10, y1=10, x2=14, y2=14),
        confidence=0.8,
        timestamp_ms=100,
        frame_index=3,
    )

    repository.save_events(job.id, [event])
    repository.save_tracks(job.id, [track])

    assert repository.get_events(job.id) == [event]
    assert repository.get_tracks(job.id) == [track]


def test_job_media_model_metadata_and_track_summaries_round_trip(repository):
    job = repository.create("/clips/match.mp4", "match.mp4")
    source = VideoMetadata(
        source_path="/clips/match.mp4",
        width=1920,
        height=1080,
        fps=25,
        frame_count=250,
        duration_ms=10_000,
        codec="h264",
    )
    output = source.model_copy(update={"source_path": "/outputs/annotated.mp4"})
    model = ModelMetadata(
        detector="ultralytics",
        model_name="yolo11n.pt",
        device="cuda:0",
        framework="ultralytics",
    )
    summary = TrackSummary(
        track_id=2,
        object_class="ball",
        start_ms=100,
        end_ms=900,
        first_frame=3,
        last_frame=23,
        observation_count=12,
        mean_confidence=0.81,
        max_confidence=0.94,
    )

    repository.save_job_metadata(job.id, source=source, output=output, model=model)
    repository.save_track_summaries(job.id, [summary])

    persisted = repository.get(job.id)
    assert persisted.metadata.source == source
    assert persisted.metadata.output == output
    assert persisted.metadata.model == model
    assert repository.get_track_summaries(job.id) == [summary]
