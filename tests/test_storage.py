from pathlib import Path

import pytest

from football_intelligence.domain import (
    BoundingBox,
    EventEvidence,
    FootballEvent,
    JobStatus,
    TrackObservation,
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


def test_job_state_machine_allows_normal_completion(repository):
    job = repository.create("/clips/match.mp4", "match.mp4")

    running = repository.transition(job.id, JobStatus.RUNNING)
    completed = repository.transition(job.id, JobStatus.COMPLETED)

    assert running.status is JobStatus.RUNNING
    assert completed.status is JobStatus.COMPLETED
    assert completed.progress == 100


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

