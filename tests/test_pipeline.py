import json
import logging
import subprocess
from pathlib import Path
from threading import Event, Thread

import cv2
import numpy as np

from football_intelligence.bus import EventBus
from football_intelligence.detection.color import ColorDetector
from football_intelligence.domain import JobStatus
from football_intelligence.pipeline import Pipeline
from football_intelligence.storage import JobRepository
from football_intelligence.tracking.iou import IoUTracker
from football_intelligence.video import probe_video


class FailFirstDetector:
    def __init__(self):
        self.delegate = ColorDetector()
        self.calls = 0

    def detect(self, frame, frame_index, timestamp_ms):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("isolated detector failure")
        return self.delegate.detect(frame, frame_index, timestamp_ms)


def make_football_clip(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (320, 180))
    assert writer.isOpened()
    ball_positions = [110, 112, 114, 150, 170, 190, 210, 230, 250, 270]
    for ball_x in ball_positions:
        frame = np.full((180, 320, 3), (30, 130, 30), dtype=np.uint8)
        cv2.rectangle(frame, (75, 55), (105, 145), (255, 0, 0), -1)
        cv2.circle(frame, (ball_x, 110), 5, (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


def test_pipeline_produces_persisted_intelligence_and_h264_video(tmp_path):
    source = tmp_path / "football.mp4"
    make_football_clip(source)
    repository = JobRepository(tmp_path / "jobs.db")
    job = repository.create(str(source), source.name)
    topics = []
    bus = EventBus()
    for topic in ["job.started", "video.opened", "event.candidate", "job.completed"]:
        bus.subscribe(topic, lambda envelope, topic=topic: topics.append(topic))
    pipeline = Pipeline(
        repository=repository,
        detector=ColorDetector(),
        tracker=IoUTracker(iou_threshold=0.1),
        output_dir=tmp_path / "outputs",
        bus=bus,
    )

    completed = pipeline.run(job.id)

    assert completed.status is JobStatus.COMPLETED
    assert completed.progress == 100
    assert completed.output_path is not None
    output = Path(completed.output_path)
    assert output.exists() and output.stat().st_size > 0
    assert len(repository.get_tracks(job.id)) >= 10
    assert repository.get_track_summaries(job.id)
    persisted = repository.get(job.id)
    assert persisted.metadata.source is not None
    assert persisted.metadata.output is not None
    assert persisted.metadata.model is not None
    assert persisted.metadata.output.codec.lower() in {"avc1", "h264", "x264"}
    assert any(event.event_type == "kick_candidate" for event in repository.get_events(job.id))
    assert topics[0:2] == ["job.started", "video.opened"]
    assert topics[-1] == "job.completed"
    assert "event.candidate" in topics

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream == {"codec_name": "h264", "width": 320, "height": 180}
    capture = cv2.VideoCapture(str(output))
    readable, frame = capture.read()
    capture.release()
    assert readable and frame.shape == (180, 320, 3)


def test_pipeline_logs_and_survives_an_isolated_frame_failure(tmp_path, caplog):
    source = tmp_path / "football.mp4"
    make_football_clip(source)
    repository = JobRepository(tmp_path / "jobs.db")
    job = repository.create(str(source), source.name)
    pipeline = Pipeline(
        repository=repository,
        detector=FailFirstDetector(),
        tracker=IoUTracker(iou_threshold=0.1),
        output_dir=tmp_path / "outputs",
    )

    with caplog.at_level(logging.WARNING):
        completed = pipeline.run(job.id)

    assert completed.status is JobStatus.COMPLETED
    assert completed.metrics["frame_errors"] == 1
    assert "isolated detector failure" in caplog.text
    assert f"job={job.id} frame=0" in caplog.text
    output_metadata = probe_video(completed.output_path)
    assert output_metadata.frame_count == 10
    assert output_metadata.duration_ms == 1000


def test_reserved_worker_honors_stop_before_pipeline_entry(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    job = repository.create(str(tmp_path / "not-opened.mp4"), "not-opened.mp4")
    repository.transition(job.id, JobStatus.RUNNING)
    repository.transition(job.id, JobStatus.STOPPING)
    pipeline = Pipeline(
        repository=repository,
        detector=ColorDetector(),
        tracker=IoUTracker(),
        output_dir=tmp_path / "outputs",
    )

    stopped = pipeline.run(job.id)

    assert stopped.status is JobStatus.STOPPED
    assert stopped.error is None


def test_stop_wins_deterministically_at_atomic_completion(tmp_path, monkeypatch):
    source = tmp_path / "football.mp4"
    make_football_clip(source)
    repository = JobRepository(tmp_path / "jobs.db")
    job = repository.create(str(source), source.name)
    completion_entered = Event()
    release_completion = Event()
    original_complete_or_stop = repository.complete_or_stop

    def paused_complete_or_stop(job_id, **kwargs):
        completion_entered.set()
        assert release_completion.wait(timeout=3)
        return original_complete_or_stop(job_id, **kwargs)

    monkeypatch.setattr(repository, "complete_or_stop", paused_complete_or_stop)
    pipeline = Pipeline(
        repository=repository,
        detector=ColorDetector(),
        tracker=IoUTracker(iou_threshold=0.1),
        output_dir=tmp_path / "outputs",
    )
    results = []
    worker = Thread(target=lambda: results.append(pipeline.run(job.id)))
    worker.start()
    assert completion_entered.wait(timeout=3)
    repository.transition(job.id, JobStatus.STOPPING)
    release_completion.set()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert results[0].status is JobStatus.STOPPED
    assert repository.get(job.id).status is JobStatus.STOPPED
