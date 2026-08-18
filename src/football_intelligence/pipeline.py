"""End-to-end video intelligence orchestration."""

import logging
import subprocess
from pathlib import Path
from time import perf_counter

import cv2

from football_intelligence.bus import EventBus
from football_intelligence.detection.base import Detector
from football_intelligence.domain import (
    FootballEvent,
    JobRecord,
    JobStatus,
    ModelMetadata,
    TrackObservation,
    VideoMetadata,
)
from football_intelligence.events import TemporalEventEngine, fuse_events
from football_intelligence.overlay import draw_overlay
from football_intelligence.storage import JobRepository
from football_intelligence.tracking.base import Tracker
from football_intelligence.tracking.summary import summarize_tracks
from football_intelligence.video import iter_frames, probe_video

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        *,
        repository: JobRepository,
        detector: Detector,
        tracker: Tracker,
        output_dir: str | Path,
        bus: EventBus | None = None,
        max_frame_errors: int = 10,
    ):
        self.repository = repository
        self.detector = detector
        self.tracker = tracker
        self.output_dir = Path(output_dir)
        self.bus = bus or EventBus()
        self.max_frame_errors = max_frame_errors

    def run(self, job_id: str) -> JobRecord:
        job = self.repository.get(job_id)
        if job.status is JobStatus.CREATED:
            job = self.repository.transition(job_id, JobStatus.RUNNING)
        elif job.status is JobStatus.STOPPING:
            stopped = self.repository.transition(job_id, JobStatus.STOPPED)
            self.bus.publish("job.stopped", {"job_id": job_id})
            return stopped
        elif job.status is not JobStatus.RUNNING:
            raise RuntimeError(f"cannot run {job.status.value} job")
        self.bus.publish("job.started", {"job_id": job_id})
        started = perf_counter()
        temporary_output: Path | None = None
        try:
            metadata = probe_video(job.source_path)
            self.repository.save_job_metadata(
                job_id,
                source=metadata,
                model=self._describe_detector(),
            )
            self.bus.publish("video.opened", {"job_id": job_id, **metadata.model_dump()})
            self.output_dir.mkdir(parents=True, exist_ok=True)
            temporary_output = self.output_dir / f"{job_id}.working.mp4"
            final_output = self.output_dir / f"{job_id}.annotated.mp4"
            writer = cv2.VideoWriter(
                str(temporary_output),
                cv2.VideoWriter_fourcc(*"mp4v"),
                metadata.fps,
                (metadata.width, metadata.height),
            )
            if not writer.isOpened():
                raise RuntimeError(f"could not open output writer: {temporary_output}")

            engine = TemporalEventEngine(job_id)
            all_tracks: list[TrackObservation] = []
            events: list[FootballEvent] = []
            event_ids: set[str] = set()
            trails: dict[int, list[tuple[int, int]]] = {}
            frame_errors = 0
            detection_seconds = 0.0
            tracking_seconds = 0.0
            overlay_seconds = 0.0
            processed_frames = 0
            try:
                for packet in iter_frames(job.source_path):
                    if self.repository.get(job_id).status is JobStatus.STOPPING:
                        stopped = self.repository.transition(job_id, JobStatus.STOPPED)
                        self.bus.publish("job.stopped", {"job_id": job_id})
                        return stopped
                    rendered = packet.frame
                    try:
                        timer = perf_counter()
                        detections = self.detector.detect(
                            packet.frame, packet.frame_index, packet.timestamp_ms
                        )
                        detection_seconds += perf_counter() - timer
                        self.bus.publish(
                            "detection.completed",
                            {
                                "job_id": job_id,
                                "frame_index": packet.frame_index,
                                "count": len(detections),
                            },
                        )

                        timer = perf_counter()
                        tracks = self.tracker.update(
                            detections, packet.frame_index, packet.timestamp_ms
                        )
                        tracking_seconds += perf_counter() - timer
                        all_tracks.extend(tracks)
                        engine.observe(tracks)
                        self.bus.publish(
                            "tracking.updated",
                            {
                                "job_id": job_id,
                                "frame_index": packet.frame_index,
                                "count": len(tracks),
                            },
                        )
                        for candidate in engine.drain_candidates():
                            if candidate.id not in event_ids:
                                event_ids.add(candidate.id)
                                events.append(candidate)
                                self.bus.publish(
                                    "event.candidate", candidate.model_dump(mode="json")
                                )

                        for track in tracks:
                            center = (
                                round(track.bbox.center[0]),
                                round(
                                    track.bbox.y2
                                    if track.object_class != "ball"
                                    else track.bbox.center[1]
                                ),
                            )
                            trails.setdefault(track.track_id, []).append(center)
                            trails[track.track_id] = trails[track.track_id][-30:]
                        active_events = [
                            event
                            for event in events
                            if event.start_ms <= packet.timestamp_ms <= event.end_ms + 1000
                        ]
                        timer = perf_counter()
                        rendered = draw_overlay(
                            packet.frame,
                            tracks,
                            active_events,
                            timestamp_ms=packet.timestamp_ms,
                            trails=trails,
                        )
                        overlay_seconds += perf_counter() - timer
                    except Exception as error:
                        frame_errors += 1
                        logger.warning(
                            "frame processing failed job=%s frame=%s error=%s",
                            job_id,
                            packet.frame_index,
                            error,
                            exc_info=True,
                        )
                        if frame_errors > self.max_frame_errors:
                            raise
                    writer.write(rendered)
                    processed_frames += 1
                    progress = min(
                        99, int((packet.frame_index + 1) * 100 / metadata.frame_count)
                    )
                    self.repository.update_progress(job_id, progress)
            finally:
                writer.release()

            fused_events = fuse_events(events)
            self.repository.save_events(job_id, fused_events)
            self.repository.save_tracks(job_id, all_tracks)
            self.repository.save_track_summaries(job_id, summarize_tracks(all_tracks))
            self._finalize_video(temporary_output, Path(job.source_path), final_output)
            output_metadata = self._validate_output(final_output, metadata)
            self.repository.save_job_metadata(job_id, output=output_metadata)
            elapsed = perf_counter() - started
            metrics: dict[str, float | int | str] = {
                "frames": processed_frames,
                "frame_errors": frame_errors,
                "total_seconds": round(elapsed, 4),
                "processing_fps": round(processed_frames / elapsed, 3) if elapsed else 0.0,
                "detector_fps": round(processed_frames / detection_seconds, 3)
                if detection_seconds
                else 0.0,
                "tracking_seconds": round(tracking_seconds, 4),
                "overlay_seconds": round(overlay_seconds, 4),
            }
            completed = self.repository.complete_or_stop(
                job_id, output_path=str(final_output), metrics=metrics
            )
            if completed.status is JobStatus.STOPPED:
                self.bus.publish("job.stopped", {"job_id": job_id})
                return completed
            self.bus.publish(
                "overlay.video.completed", {"job_id": job_id, "path": str(final_output)}
            )
            self.bus.publish("job.completed", {"job_id": job_id, "metrics": metrics})
            return completed
        except Exception as error:
            logger.exception("pipeline failed job=%s error=%s", job_id, error)
            current = self.repository.get(job_id)
            if current.status is JobStatus.STOPPING:
                stopped = self.repository.transition(job_id, JobStatus.STOPPED)
                self.bus.publish("job.stopped", {"job_id": job_id})
                return stopped
            if current.status is JobStatus.RUNNING:
                self.repository.transition(job_id, JobStatus.FAILED, error=str(error))
            self.bus.publish("job.failed", {"job_id": job_id, "error": str(error)})
            raise
        finally:
            if temporary_output is not None:
                temporary_output.unlink(missing_ok=True)

    @staticmethod
    def _finalize_video(working: Path, source: Path, output: Path) -> None:
        command = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(working),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            str(output),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)

    @staticmethod
    def _validate_output(output: Path, expected: VideoMetadata) -> VideoMetadata:
        actual = probe_video(output)
        expected_geometry = (expected.width, expected.height)
        actual_geometry = (actual.width, actual.height)
        frame_tolerance_ms = round(1000 / expected.fps)
        if actual_geometry != expected_geometry:
            raise RuntimeError(
                f"output geometry changed: expected {expected_geometry}, got {actual_geometry}"
            )
        if abs(actual.fps - expected.fps) > 0.01:
            raise RuntimeError(f"output FPS changed: expected {expected.fps}, got {actual.fps}")
        if actual.frame_count != expected.frame_count:
            raise RuntimeError(
                f"output frame count changed: expected {expected.frame_count}, "
                f"got {actual.frame_count}"
            )
        if abs(actual.duration_ms - expected.duration_ms) > frame_tolerance_ms:
            raise RuntimeError(
                f"output duration changed: expected {expected.duration_ms}, "
                f"got {actual.duration_ms}"
            )
        if actual.codec.lower() not in {"avc1", "h264", "x264"}:
            raise RuntimeError(f"output is not H.264: codec={actual.codec}")
        return actual

    def _describe_detector(self) -> ModelMetadata:
        detector_name = type(self.detector).__name__
        is_ultralytics = detector_name == "UltralyticsDetector"
        return ModelMetadata(
            detector=detector_name,
            model_name=str(
                getattr(
                    self.detector,
                    "model_name",
                    "yolo11n.pt" if is_ultralytics else "deterministic-color-contours",
                )
            ),
            device=str(getattr(self.detector, "device", "cpu") or "auto"),
            framework="ultralytics" if is_ultralytics else f"opencv-{cv2.__version__}",
        )
