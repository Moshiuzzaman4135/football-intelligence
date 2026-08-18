"""Job deletion coverage for both repositories and the API."""

from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from football_intelligence.api import create_app
from football_intelligence.detection.color import ColorDetector
from football_intelligence.domain import (
    EventEvidence,
    FootballEvent,
    JobStatus,
)
from football_intelligence.persistence import (
    SQLAlchemyJobRepository,
    create_persistence_engine,
    create_schema,
)
from football_intelligence.pipeline import Pipeline
from football_intelligence.storage import JobNotFound, JobRepository
from football_intelligence.tracking.iou import IoUTracker


def make_clip(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (320, 180))
    assert writer.isOpened()
    ball_positions = [110, 112, 114, 150, 170, 190, 210, 230, 250, 270]
    for ball_x in ball_positions:
        frame = np.full((180, 320, 3), (30, 130, 30), dtype=np.uint8)
        cv2.rectangle(frame, (75, 55), (105, 145), (255, 0, 0), -1)
        cv2.circle(frame, (ball_x, 110), 5, (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


def make_event(job_id: str) -> FootballEvent:
    return FootballEvent(
        job_id=job_id,
        event_type="kick_candidate",
        start_ms=100,
        end_ms=200,
        description="test event",
        confidence=0.8,
        evidence=[EventEvidence(kind="ball_speed_px_s", value=100.0, confidence=0.8)],
        source=["heuristic.temporal"],
    )


def test_legacy_repository_delete_removes_row(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    job = repository.create("/tmp/match.mp4", "match.mp4")

    repository.delete(job.id)

    with pytest.raises(JobNotFound):
        repository.get(job.id)
    assert repository.list() == []


def test_sqlalchemy_repository_delete_clears_payloads(tmp_path):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'production.db'}")
    create_schema(engine)
    repository = SQLAlchemyJobRepository(engine)
    job = repository.create("/tmp/match.mp4", "match.mp4")
    repository.save_events(job.id, [make_event(job.id)])
    repository.save_job_metadata(job.id, source=None, output=None, model=None)
    assert repository.get_events(job.id)

    repository.delete(job.id)

    with pytest.raises(JobNotFound):
        repository.get(job.id)
    assert repository.list() == []


def test_api_delete_removes_job_artifacts_and_row(tmp_path):
    source = tmp_path / "football.mp4"
    make_clip(source)
    repository = JobRepository(tmp_path / "jobs.db")
    job = repository.create(str(source), source.name)
    pipeline = Pipeline(
        repository=repository,
        detector=ColorDetector(),
        tracker=IoUTracker(iou_threshold=0.1),
        output_dir=tmp_path / "outputs",
    )
    completed = pipeline.run(job.id)
    assert completed.status is JobStatus.COMPLETED
    event = repository.get_events(job.id)[0]
    output = Path(completed.output_path)
    tracks = tmp_path / "outputs" / f"{job.id}.tracks.json"
    assert output.is_file() and tracks.is_file()

    app = create_app(repository=repository, data_root=tmp_path)
    with TestClient(app) as client:
        # materialize a clip and thumbnail for the event
        assert client.get(f"/jobs/{job.id}/events/{event.id}/clip").status_code == 200
        clip_file = tmp_path / "clips" / f"{event.id}.mp4"
        assert clip_file.is_file()

        response = client.delete(f"/jobs/{job.id}")

    assert response.status_code == 204
    assert not output.exists()
    assert not tracks.exists()
    assert not clip_file.exists()
    with pytest.raises(JobNotFound):
        repository.get(job.id)


def test_api_delete_rejects_running_job(tmp_path):
    source = tmp_path / "football.mp4"
    make_clip(source)
    repository = JobRepository(tmp_path / "jobs.db")
    job = repository.create(str(source), source.name)
    repository.transition(job.id, JobStatus.RUNNING)
    app = create_app(repository=repository, data_root=tmp_path)

    with TestClient(app) as client:
        response = client.delete(f"/jobs/{job.id}")

    assert response.status_code == 409
    assert repository.get(job.id).status is JobStatus.RUNNING


def test_api_delete_keeps_external_source_file(tmp_path):
    source = tmp_path / "external.mp4"
    make_clip(source)
    repository = JobRepository(tmp_path / "jobs.db")
    job = repository.create(str(source), source.name)
    app = create_app(repository=repository, data_root=tmp_path)

    with TestClient(app) as client:
        response = client.delete(f"/jobs/{job.id}")

    assert response.status_code == 204
    assert source.is_file(), "external source files must never be deleted"
