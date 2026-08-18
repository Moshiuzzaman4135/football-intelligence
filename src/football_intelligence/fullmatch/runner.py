"""Restartable, bounded, single-host full-match processing runner."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import cv2
from pydantic import BaseModel, Field

from football_intelligence.detection.base import Detector
from football_intelligence.detection.color import ColorDetector
from football_intelligence.domain import (
    FootballEvent,
    JobRecord,
    JobStatus,
    ScoreboardObservation,
    VideoMetadata,
)
from football_intelligence.events import TemporalEventEngine, deduplicate_events
from football_intelligence.fullmatch.heatmap import ScreenSpaceHeatMap
from football_intelligence.fullmatch.manifest import (
    ChunkRecord,
    FinalArtifact,
    FullMatchManifest,
    ManifestStore,
    RawOcrEvidence,
    RunnerOptions,
)
from football_intelligence.fullmatch.media import (
    MediaProbe,
    build_proxy,
    localize_s3_source,
    probe_media,
    sha256_file,
    validate_source_media,
)
from football_intelligence.fullmatch.ocr import (
    FakeOcrEngine,
    OcrEngine,
    ScoreboardConsensus,
    ScoreboardParser,
)
from football_intelligence.object_store import ObjectStore
from football_intelligence.overlay import draw_overlay
from football_intelligence.persistence import JobStore
from football_intelligence.tracking.base import Tracker
from football_intelligence.tracking.iou import IoUTracker

TRACK_NAMESPACE_SIZE = 1_000_000


def namespace_track_id(chunk_index: int, local_track_id: int) -> int:
    if chunk_index < 0 or not 0 <= local_track_id < TRACK_NAMESPACE_SIZE:
        raise ValueError("track namespace values are out of range")
    return chunk_index * TRACK_NAMESPACE_SIZE + local_track_id


class ChunkResult(BaseModel):
    path: Path
    events: list[FootballEvent] = Field(default_factory=list)
    scoreboard: list[ScoreboardObservation] = Field(default_factory=list)
    raw_ocr_evidence: list[RawOcrEvidence] = Field(default_factory=list)
    heat_map_counts: list[list[int]]
    peak_observations: int = Field(default=0, ge=0)


class FullMatchRunner:
    """Runs at most one chunk in memory and checkpoints after every chunk."""

    def __init__(
        self,
        *,
        repository: JobStore,
        object_store: ObjectStore,
        bucket: str,
        data_root: str | Path,
        detector_factory: Callable[[], Detector] = ColorDetector,
        tracker_factory: Callable[[], Tracker] = IoUTracker,
        ocr_engine: OcrEngine | None = None,
        options: RunnerOptions | None = None,
        max_frame_errors: int = 10,
    ) -> None:
        self.repository = repository
        self.object_store = object_store
        self.bucket = bucket
        self.data_root = Path(data_root)
        self.detector_factory = detector_factory
        self.tracker_factory = tracker_factory
        self.ocr_engine = ocr_engine or FakeOcrEngine([])
        self.options = options or RunnerOptions()
        self.max_frame_errors = max_frame_errors
        self._consensus: ScoreboardConsensus | None = None

    def workspace_for(self, job_id: str) -> Path:
        return self.data_root / "fullmatch" / job_id

    def status(self, job_id: str) -> FullMatchManifest:
        return ManifestStore(self.workspace_for(job_id) / "manifest.json").load()

    def scoreboard(self, job_id: str) -> list[ScoreboardObservation]:
        manifest = self.status(job_id)
        return [item for chunk in manifest.chunks for item in chunk.scoreboard]

    def heat_map_path(self, job_id: str) -> Path:
        manifest = self.status(job_id)
        if manifest.final_artifact is None:
            raise RuntimeError("heat map is not ready")
        return Path(manifest.final_artifact.heat_map_path)

    def run(self, job_id: str) -> JobRecord:
        job = self.repository.get(job_id)
        if job.status is JobStatus.CREATED:
            job = self.repository.transition(job_id, JobStatus.RUNNING)
        elif job.status is JobStatus.COMPLETED:
            return job
        elif job.status is not JobStatus.RUNNING:
            raise RuntimeError(f"cannot run {job.status.value} full-match job")
        workspace = self.workspace_for(job_id)
        workspace.mkdir(parents=True, exist_ok=True)
        store = ManifestStore(workspace / "manifest.json")
        if store.exists():
            manifest = store.load()
            if manifest.job_id != job.id or manifest.source_uri != job.source_path:
                raise RuntimeError("manifest identity does not match the job")
            if manifest.options != self.options:
                raise RuntimeError("runner options differ from the immutable manifest")
        else:
            source, proxy = self._prepare_media(job, workspace)
            manifest = FullMatchManifest.create(
                job_id=job.id,
                source_uri=job.source_path,
                options=self.options,
                source=source,
                proxy=proxy,
            )
            store.save(manifest)
            self.repository.save_job_metadata(job_id, source=self._video_metadata(source))
        repaired_manifest = False
        for index, chunk in enumerate(manifest.chunks):
            if chunk.status == "completed" and not self._chunk_is_durable(chunk):
                manifest.chunks[index] = chunk.model_copy(
                    update={
                        "status": "pending",
                        "output_path": None,
                        "sha256": None,
                        "events": [],
                        "scoreboard": [],
                        "raw_ocr_evidence": [],
                        "heat_map_counts": None,
                        "peak_observations": 0,
                        "completed_at": None,
                    }
                )
                repaired_manifest = True
        if repaired_manifest:
            store.save(manifest)
        proxy_path = Path(manifest.proxy.path)
        self._consensus = ScoreboardConsensus(job_id)
        durable_scoreboard = [
            observation
            for chunk in manifest.chunks
            if chunk.status == "completed"
            for observation in chunk.scoreboard
        ]
        if durable_scoreboard:
            self._consensus.seed(durable_scoreboard[-1])
        try:
            for index, chunk in enumerate(manifest.chunks):
                if chunk.status == "completed":
                    continue
                current = self.repository.get(job_id)
                if current.status is JobStatus.STOPPING:
                    return self.repository.transition(job_id, JobStatus.STOPPED)
                manifest.chunks[index] = chunk.model_copy(update={"status": "running"})
                store.save(manifest)
                result = self._process_chunk(
                    job_id=job_id,
                    proxy=proxy_path,
                    chunk=manifest.chunks[index],
                )
                completed = manifest.chunks[index].model_copy(
                    update={
                        "status": "completed",
                        "output_path": str(result.path),
                        "sha256": sha256_file(result.path),
                        "events": result.events,
                        "scoreboard": result.scoreboard,
                        "raw_ocr_evidence": result.raw_ocr_evidence,
                        "heat_map_counts": result.heat_map_counts,
                        "peak_observations": result.peak_observations,
                        "completed_at": datetime.now(UTC),
                    }
                )
                manifest.chunks[index] = completed
                manifest.peak_observations = max(
                    manifest.peak_observations, result.peak_observations
                )
                store.save(manifest)
                self._persist_events(manifest)
                self.repository.update_progress(job_id, manifest.progress)
            heat_map = self._aggregate_heat_map(manifest)
            heat_map_path = heat_map.write_png(workspace / "heat-map.png")
            output = self._finalize(
                manifest=manifest, proxy=proxy_path, workspace=workspace
            )
            manifest.final_artifact = FinalArtifact(
                path=str(output),
                sha256=sha256_file(output),
                heat_map_path=str(heat_map_path),
                heat_map_sha256=sha256_file(heat_map_path),
                completed_at=datetime.now(UTC),
            )
            store.save(manifest)
            self._persist_events(manifest)
            output_probe = self._probe_final(output)
            if output_probe is not None:
                self.repository.save_job_metadata(
                    job_id, output=self._video_metadata(output_probe)
                )
            return self.repository.complete_or_stop(
                job_id,
                output_path=str(output),
                metrics={
                    "full_match": "mvp",
                    "chunks": len(manifest.chunks),
                    "peak_observations": manifest.peak_observations,
                },
            )
        except Exception as error:
            current = self.repository.get(job_id)
            if current.status is JobStatus.STOPPING:
                return self.repository.transition(job_id, JobStatus.STOPPED)
            if current.status is JobStatus.RUNNING:
                self.repository.transition(job_id, JobStatus.FAILED, error=str(error))
            raise

    def _prepare_media(
        self, job: JobRecord, workspace: Path
    ) -> tuple[MediaProbe, MediaProbe]:
        existing_sources = list(workspace.glob("source.*"))
        source_path = next(
            (path for path in existing_sources if path.name != "source.partial"), None
        )
        if source_path is None:
            source_path = localize_s3_source(
                self.object_store,
                job.source_path,
                bucket=self.bucket,
                destination_dir=workspace,
            )
        source = probe_media(source_path)
        validate_source_media(source)
        proxy_path = workspace / "proxy.mp4"
        if not proxy_path.is_file():
            build_proxy(source_path, proxy_path)
        proxy = probe_media(proxy_path)
        return source, proxy

    def _probe_final(self, output: Path) -> MediaProbe | None:
        return probe_media(output)

    @staticmethod
    def _chunk_is_durable(chunk: ChunkRecord) -> bool:
        if chunk.output_path is None or chunk.sha256 is None:
            return False
        output = Path(chunk.output_path)
        return output.is_file() and sha256_file(output) == chunk.sha256

    def _process_chunk(
        self, *, job_id: str, proxy: Path, chunk: ChunkRecord
    ) -> ChunkResult:
        detector = self.detector_factory()
        tracker = self.tracker_factory()
        engine = TemporalEventEngine(job_id)
        parser = ScoreboardParser()
        heat_map = ScreenSpaceHeatMap()
        events: list[FootballEvent] = []
        scoreboard: list[ScoreboardObservation] = []
        raw_ocr_evidence: list[RawOcrEvidence] = []
        capture = cv2.VideoCapture(str(proxy))
        if not capture.isOpened():
            raise RuntimeError(f"could not open proxy {proxy}")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        start_frame = round(chunk.context_start_ms * fps / 1_000)
        end_frame = round(chunk.end_ms * fps / 1_000)
        capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        work_path = self.workspace_for(job_id) / f"chunk-{chunk.index:04d}.working.mp4"
        output_path = self.workspace_for(job_id) / f"chunk-{chunk.index:04d}.mp4"
        writer = cv2.VideoWriter(
            str(work_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
        )
        if not writer.isOpened():
            capture.release()
            raise RuntimeError(f"could not open chunk writer {work_path}")
        trails: dict[int, list[tuple[int, int]]] = {}
        peak_observations = 0
        frame_errors = 0
        last_ocr_ms = -self.options.ocr_interval_ms
        try:
            for frame_index in range(start_frame, end_frame):
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f"proxy decode stopped at frame {frame_index}")
                timestamp_ms = round(frame_index * 1_000 / fps)
                if (
                    frame_index % max(1, round(fps)) == 0
                    and self.repository.get(job_id).status is JobStatus.STOPPING
                ):
                    raise RuntimeError("full-match stop requested")
                rendered = frame
                try:
                    detections = detector.detect(frame, frame_index, timestamp_ms)
                    local_tracks = tracker.update(detections, frame_index, timestamp_ms)
                    tracks = [
                        track.model_copy(
                            update={
                                "track_id": namespace_track_id(
                                    chunk.index, track.track_id
                                )
                            }
                        )
                        for track in local_tracks
                    ]
                    peak_observations = max(peak_observations, len(tracks))
                    engine.observe(tracks)
                    if timestamp_ms >= chunk.output_start_ms:
                        heat_map.observe(
                            tracks, frame_width=width, frame_height=height
                        )
                        if timestamp_ms - last_ocr_ms >= self.options.ocr_interval_ms:
                            last_ocr_ms = timestamp_ms
                            raw = self.ocr_engine.read(
                                frame, self.options.scoreboard_region
                            )
                            raw_ocr_evidence.append(
                                RawOcrEvidence(
                                    timestamp_ms=timestamp_ms,
                                    frame_index=frame_index,
                                    raw_text=raw.text,
                                    raw_confidence=raw.confidence,
                                )
                            )
                            parsed = parser.parse(
                                raw,
                                timestamp_ms=timestamp_ms,
                                frame_index=frame_index,
                                region=self.options.scoreboard_region,
                            )
                            if parsed is not None and self._consensus is not None:
                                accepted, score_event = self._consensus.observe(parsed)
                                if accepted is not None:
                                    scoreboard.append(accepted)
                                if score_event is not None:
                                    events.append(score_event)
                    for candidate in engine.drain_candidates():
                        if candidate.end_ms >= chunk.output_start_ms:
                            events.append(candidate)
                    for track in tracks:
                        center = tuple(round(value) for value in track.bbox.center)
                        trails.setdefault(track.track_id, []).append(center)
                        trails[track.track_id] = trails[track.track_id][-30:]
                    rendered = draw_overlay(
                        frame,
                        tracks,
                        events[-10:],
                        timestamp_ms=timestamp_ms,
                        trails=trails,
                    )
                except Exception:
                    frame_errors += 1
                    if frame_errors > self.max_frame_errors:
                        raise
                if timestamp_ms >= chunk.output_start_ms:
                    writer.write(rendered)
        finally:
            writer.release()
            capture.release()
        temporary = output_path.with_name(f"{output_path.stem}.partial.mp4")
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(work_path),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    str(temporary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            temporary.replace(output_path)
        finally:
            work_path.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
        return ChunkResult(
            path=output_path,
            events=deduplicate_events(events),
            scoreboard=scoreboard,
            raw_ocr_evidence=raw_ocr_evidence,
            heat_map_counts=heat_map.to_counts(),
            peak_observations=peak_observations,
        )

    def _finalize(
        self, *, manifest: FullMatchManifest, proxy: Path, workspace: Path
    ) -> Path:
        concat_file = workspace / "chunks.txt"
        concat_file.write_text(
            "".join(
                f"file '{Path(chunk.output_path).resolve()}'\n"
                for chunk in manifest.chunks
                if chunk.output_path
            ),
            encoding="utf-8",
        )
        video_only = workspace / "annotated-video-only.mp4"
        temporary = workspace / "annotated.partial.mp4"
        output = workspace / "annotated.mp4"
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-c",
                    "copy",
                    str(video_only),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(video_only),
                    "-i",
                    str(proxy),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a?",
                    "-vf",
                    f"setpts=N/({manifest.proxy.fps:g}*TB)",
                    "-r",
                    f"{manifest.proxy.fps:g}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(temporary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            actual = probe_media(temporary)
            self._validate_final(actual, manifest.proxy)
            temporary.replace(output)
        finally:
            video_only.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
        return output

    @staticmethod
    def _validate_final(actual: MediaProbe, expected: MediaProbe) -> None:
        if actual.video_codec != "h264":
            raise RuntimeError("annotated video is not H.264")
        if (actual.width, actual.height) != (expected.width, expected.height):
            raise RuntimeError("annotated video geometry changed")
        if abs(actual.fps - expected.fps) > 0.01:
            raise RuntimeError("annotated video FPS changed")
        if abs(actual.duration_ms - expected.duration_ms) > max(
            200, round(2_000 / expected.fps)
        ):
            raise RuntimeError("annotated video duration changed")
        if expected.has_audio and not actual.has_audio:
            raise RuntimeError("annotated video lost audio")

    def _persist_events(self, manifest: FullMatchManifest) -> None:
        events = [event for chunk in manifest.chunks for event in chunk.events]
        self.repository.save_events(manifest.job_id, deduplicate_events(events))

    @staticmethod
    def _aggregate_heat_map(manifest: FullMatchManifest) -> ScreenSpaceHeatMap:
        total = [[0] * ScreenSpaceHeatMap.columns for _ in range(ScreenSpaceHeatMap.rows)]
        for chunk in manifest.chunks:
            if chunk.heat_map_counts is None:
                continue
            for row in range(ScreenSpaceHeatMap.rows):
                for column in range(ScreenSpaceHeatMap.columns):
                    total[row][column] += chunk.heat_map_counts[row][column]
        return ScreenSpaceHeatMap.from_counts(total)

    @staticmethod
    def _video_metadata(probe: MediaProbe) -> VideoMetadata:
        return VideoMetadata(
            source_path=probe.path,
            width=probe.width,
            height=probe.height,
            fps=probe.fps,
            frame_count=probe.frame_count,
            duration_ms=probe.duration_ms,
            codec=probe.video_codec,
        )
