"""Event clip and thumbnail extraction tests."""

from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from football_intelligence.api import create_app
from football_intelligence.clips import ClipError, build_event_clip, build_event_thumbnail
from football_intelligence.detection.color import ColorDetector
from football_intelligence.domain import JobStatus
from football_intelligence.pipeline import Pipeline
from football_intelligence.storage import JobRepository
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


def test_build_event_clip_produces_playable_mp4(tmp_path):
    source = tmp_path / "source.mp4"
    make_clip(source)
    output = tmp_path / "clips" / "event.mp4"

    result = build_event_clip(source, output, start_ms=1500)

    assert result == output and output.is_file() and output.stat().st_size > 0
    assert output.read_bytes()[4:8] == b"ftyp"


def test_build_event_thumbnail_produces_png(tmp_path):
    source = tmp_path / "source.mp4"
    make_clip(source)
    output = tmp_path / "clips" / "event.png"

    result = build_event_thumbnail(source, output, at_ms=500)

    assert result == output and output.is_file()
    assert output.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_build_event_clip_rejects_missing_source(tmp_path):
    with pytest.raises(ClipError, match="missing"):
        build_event_clip(tmp_path / "missing.mp4", tmp_path / "out.mp4", start_ms=0)


def test_build_event_clip_rejects_invalid_window(tmp_path):
    source = tmp_path / "source.mp4"
    make_clip(source)

    with pytest.raises(ClipError):
        build_event_clip(source, tmp_path / "out.mp4", start_ms=0, duration_ms=0)


def test_event_clip_and_thumbnail_api(tmp_path):
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
    events = repository.get_events(job.id)
    assert events
    event = events[0]
    app = create_app(repository=repository, data_root=tmp_path)

    with TestClient(app) as client:
        clip = client.get(f"/jobs/{job.id}/events/{event.id}/clip")
        assert clip.status_code == 200
        assert clip.headers["content-type"].startswith("video/mp4")
        assert clip.content[4:8] == b"ftyp"

        thumbnail = client.get(f"/jobs/{job.id}/events/{event.id}/thumbnail")
        assert thumbnail.status_code == 200
        assert thumbnail.headers["content-type"].startswith("image/png")
        assert thumbnail.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_event_clip_returns_404_for_unknown_event(tmp_path):
    source = tmp_path / "football.mp4"
    make_clip(source)
    repository = JobRepository(tmp_path / "jobs.db")
    job = repository.create(str(source), source.name)
    app = create_app(repository=repository, data_root=tmp_path)

    with TestClient(app) as client:
        response = client.get(f"/jobs/{job.id}/events/missing/clip")

    assert response.status_code == 404
