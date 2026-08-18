from pathlib import Path

import cv2
import numpy as np
import pytest

import football_intelligence.video as video_module
from football_intelligence.demo_fixture import generate_demo_clip
from football_intelligence.detection.color import ColorDetector
from football_intelligence.domain import VideoMetadata
from football_intelligence.events import TemporalEventEngine
from football_intelligence.tracking.iou import IoUTracker
from football_intelligence.video import VideoDecodeError, VideoOpenError, iter_frames, probe_video


@pytest.fixture
def generated_video(tmp_path: Path) -> Path:
    path = tmp_path / "moving-ball.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (160, 96))
    assert writer.isOpened()
    for frame_index in range(10):
        frame = np.full((96, 160, 3), (20, 120, 20), dtype=np.uint8)
        cv2.circle(frame, (20 + frame_index * 8, 48), 4, (255, 255, 255), -1)
        writer.write(frame)
    writer.release()
    return path


def test_probe_video_returns_source_timebase_and_geometry(generated_video):
    metadata = probe_video(generated_video)

    assert metadata.width == 160
    assert metadata.height == 96
    assert metadata.fps == pytest.approx(10.0, rel=0.02)
    assert metadata.frame_count == 10
    assert metadata.duration_ms == pytest.approx(1000, abs=20)


def test_iter_frames_preserves_original_indexes_and_timestamps(generated_video):
    packets = list(iter_frames(generated_video, stride=3))

    assert [packet.frame_index for packet in packets] == [0, 3, 6, 9]
    assert [packet.timestamp_ms for packet in packets] == [0, 300, 600, 900]
    assert packets[0].frame.shape == (96, 160, 3)


def test_probe_video_rejects_missing_source(tmp_path):
    with pytest.raises(VideoOpenError, match="could not open video"):
        probe_video(tmp_path / "missing.mp4")


def test_iter_frames_rejects_premature_decode_eof(monkeypatch):
    metadata = VideoMetadata(
        source_path="broken.mp4",
        width=16,
        height=16,
        fps=10,
        frame_count=3,
        duration_ms=300,
        codec="mp4v",
    )

    class PrematureCapture:
        def __init__(self):
            self.reads = 0

        def read(self):
            self.reads += 1
            if self.reads <= 2:
                return True, np.zeros((16, 16, 3), dtype=np.uint8)
            return False, None

        def release(self):
            pass

    monkeypatch.setattr(video_module, "probe_video", lambda _path: metadata)
    monkeypatch.setattr(video_module.cv2, "VideoCapture", lambda _path: PrematureCapture())

    with pytest.raises(VideoDecodeError, match="ended at frame 2 of 3"):
        list(iter_frames("broken.mp4"))


def test_demo_fixture_generator_creates_short_repeatable_football_clip(tmp_path):
    path = generate_demo_clip(tmp_path / "demo.mp4")

    metadata = probe_video(path)
    assert (metadata.width, metadata.height, metadata.frame_count) == (640, 360, 300)
    assert metadata.duration_ms == 30_000

    detector = ColorDetector()
    tracker = IoUTracker(iou_threshold=0.1)
    engine = TemporalEventEngine("demo")
    for packet in iter_frames(path):
        detections = detector.detect(packet.frame, packet.frame_index, packet.timestamp_ms)
        engine.observe(tracker.update(detections, packet.frame_index, packet.timestamp_ms))
    assert any(event.event_type == "kick_candidate" for event in engine.finalize())
