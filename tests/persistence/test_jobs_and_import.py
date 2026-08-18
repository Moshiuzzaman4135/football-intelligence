from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import event

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
from football_intelligence.persistence import (
    JobStore,
    RawObservationPersistenceError,
    SQLAlchemyJobRepository,
    create_persistence_engine,
    create_schema,
    import_legacy_sqlite,
)
from football_intelligence.storage import InvalidJobTransition, JobRepository


def _new_repository(path: Path) -> SQLAlchemyJobRepository:
    engine = create_persistence_engine(f"sqlite:///{path}")
    create_schema(engine)
    return SQLAlchemyJobRepository(engine)


def test_sqlalchemy_job_repository_preserves_job_store_behavior(tmp_path: Path):
    repository = _new_repository(tmp_path / "production.db")
    job = repository.create("/clips/match.mp4", "match.mp4")

    running = repository.transition(job.id, JobStatus.RUNNING)
    repository.update_progress(job.id, 45)
    completed = repository.complete_or_stop(
        job.id,
        output_path="/outputs/match.mp4",
        metrics={"frames": 250},
    )

    assert isinstance(repository, JobStore)
    assert running.status is JobStatus.RUNNING
    assert completed.status is JobStatus.COMPLETED
    assert completed.progress == 100
    assert completed.output_path == "/outputs/match.mp4"
    assert repository.list() == [completed]


def test_concurrent_job_transitions_use_compare_and_set(tmp_path: Path):
    repository = _new_repository(tmp_path / "production.db")
    job = repository.create("/clips/match.mp4", "match.mp4")
    repository.transition(job.id, JobStatus.RUNNING)

    def transition(target: JobStatus):
        try:
            return repository.transition(job.id, target)
        except InvalidJobTransition:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(transition, [JobStatus.COMPLETED, JobStatus.FAILED]))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert repository.get(job.id).status in {JobStatus.COMPLETED, JobStatus.FAILED}


def test_legacy_sqlite_import_is_complete_repeatable_and_read_only(tmp_path: Path):
    legacy_path = tmp_path / "legacy.db"
    legacy = JobRepository(legacy_path)
    job = legacy.create("/clips/match.mp4", "match.mp4")
    source = VideoMetadata(
        source_path="/clips/match.mp4",
        width=1920,
        height=1080,
        fps=25,
        frame_count=250,
        duration_ms=10_000,
        codec="h264",
    )
    model = ModelMetadata(
        detector="ultralytics",
        model_name="yolo11n.pt",
        device="cuda:0",
        framework="ultralytics",
    )
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
    summary = TrackSummary(
        track_id=2,
        object_class="ball",
        start_ms=100,
        end_ms=300,
        first_frame=3,
        last_frame=8,
        observation_count=6,
        mean_confidence=0.8,
        max_confidence=0.9,
    )
    legacy.save_job_metadata(job.id, source=source, model=model)
    legacy.save_events(job.id, [event])
    legacy.save_tracks(job.id, [track])
    legacy.save_track_summaries(job.id, [summary])
    original_digest = sha256(legacy_path.read_bytes()).hexdigest()
    target = _new_repository(tmp_path / "production.db")

    first = import_legacy_sqlite(legacy_path, target)
    second = import_legacy_sqlite(legacy_path, target)

    assert first.imported_jobs == 1
    assert first.skipped_jobs == 0
    assert first.skipped_track_observations == 1
    assert second.imported_jobs == 0
    assert second.skipped_jobs == 1
    assert second.skipped_track_observations == 1
    assert target.get(job.id) == legacy.get(job.id)
    assert target.get(job.id).metadata.source == source
    assert target.get(job.id).metadata.model == model
    assert target.get_events(job.id) == [event]
    assert target.get_tracks(job.id) == []
    assert target.get_track_summaries(job.id) == [summary]
    assert sha256(legacy_path.read_bytes()).hexdigest() == original_digest


def test_sqlalchemy_repository_rejects_raw_track_observation_rows(tmp_path: Path):
    repository = _new_repository(tmp_path / "production.db")
    job = repository.create("/clips/match.mp4", "match.mp4")
    track = TrackObservation(
        track_id=2,
        object_class="ball",
        bbox=BoundingBox(x1=10, y1=10, x2=14, y2=14),
        confidence=0.8,
        timestamp_ms=100,
        frame_index=3,
    )

    with pytest.raises(RawObservationPersistenceError, match="external artifact"):
        repository.save_tracks(job.id, [track])

    assert repository.get_tracks(job.id) == []


def test_concurrent_legacy_importers_finish_with_one_copy(tmp_path: Path):
    legacy_path = tmp_path / "legacy.db"
    legacy = JobRepository(legacy_path)
    job = legacy.create("/clips/match.mp4", "match.mp4")
    target = _new_repository(tmp_path / "production.db")
    insert_barrier = Barrier(2)

    @event.listens_for(target.engine, "before_cursor_execute")
    def synchronize_job_inserts(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("INSERT INTO JOBS"):
            insert_barrier.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: import_legacy_sqlite(legacy_path, target), range(2)))

    assert sum(result.imported_jobs for result in results) == 1
    assert sum(result.skipped_jobs for result in results) == 1
    assert target.list() == [target.get(job.id)]
