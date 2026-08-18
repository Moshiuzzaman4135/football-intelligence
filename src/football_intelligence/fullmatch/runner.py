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
    Detection,
    FootballEvent,
    JobRecord,
    JobStatus,
    ScoreboardObservation,
    TrackObservation,
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
    RuntimeProvenance,
)
from football_intelligence.fullmatch.media import (
    MediaCancelled,
    MediaProbe,
    build_proxy,
    delete_file_durable,
    localize_s3_source,
    probe_media,
    run_media_command,
    sha256_file,
    validate_source_media,
)
from football_intelligence.fullmatch.media import (
    validate_full_decode as validate_media_decode,
)
from football_intelligence.fullmatch.ocr import (
    ConsensusState,
    FakeOcrEngine,
    OcrEngine,
    ScoreboardConsensus,
    ScoreboardParser,
)
from football_intelligence.fullmatch.provenance import measure_runtime_provenance
from football_intelligence.object_store import ObjectStore
from football_intelligence.overlay import draw_overlay
from football_intelligence.persistence import JobStore
from football_intelligence.quality import QualityOptions, apply_confidence_thresholds
from football_intelligence.tracking.base import Tracker
from football_intelligence.tracking.iou import IoUTracker
from football_intelligence.trails import TrailBuffer

TRACK_NAMESPACE_SIZE = 1_000_000


def namespace_track_id(chunk_index: int, local_track_id: int) -> int:
    if chunk_index < 0 or not 0 <= local_track_id < TRACK_NAMESPACE_SIZE:
        raise ValueError("track namespace values are out of range")
    return chunk_index * TRACK_NAMESPACE_SIZE + local_track_id


def active_events_at(
    events: list[FootballEvent], timestamp_ms: int, post_window_ms: int = 1_000
) -> list[FootballEvent]:
    return [
        event
        for event in events
        if event.start_ms <= timestamp_ms <= event.end_ms + post_window_ms
    ]


class ChunkResult(BaseModel):
    path: Path
    events: list[FootballEvent] = Field(default_factory=list)
    scoreboard: list[ScoreboardObservation] = Field(default_factory=list)
    raw_ocr_evidence: list[RawOcrEvidence] = Field(default_factory=list)
    consensus_state: ConsensusState | None = None
    heat_map_counts: list[list[int]]
    peak_observations: int = Field(default=0, ge=0)


class BoundedTrails:
    def __init__(
        self, *, max_points: int = 30, max_inactive_frames: int = 30, max_tracks: int = 512
    ) -> None:
        self.max_points = max_points
        self.max_inactive_frames = max_inactive_frames
        self.max_tracks = max_tracks
        self._trails: dict[int, list[tuple[int, int]]] = {}
        self._last_seen: dict[int, int] = {}

    def update(
        self, tracks: list[TrackObservation], frame_index: int
    ) -> dict[int, list[tuple[int, int]]]:
        for track in tracks:
            center = tuple(round(value) for value in track.bbox.center)
            self._trails.setdefault(track.track_id, []).append(center)
            self._trails[track.track_id] = self._trails[track.track_id][
                -self.max_points :
            ]
            self._last_seen[track.track_id] = frame_index
        expired = [
            track_id
            for track_id, last_seen in self._last_seen.items()
            if frame_index - last_seen > self.max_inactive_frames
        ]
        overflow = max(0, len(self._last_seen) - self.max_tracks)
        if overflow:
            expired.extend(
                track_id
                for track_id, _ in sorted(
                    self._last_seen.items(), key=lambda item: item[1]
                )[:overflow]
            )
        for track_id in set(expired):
            self._last_seen.pop(track_id, None)
            self._trails.pop(track_id, None)
        return self._trails


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
        provenance: RuntimeProvenance | None = None,
        max_frame_errors: int | None = None,
        quality: QualityOptions | None = None,
    ) -> None:
        self.repository = repository
        self.object_store = object_store
        self.bucket = bucket
        self.data_root = Path(data_root)
        self.detector_factory = detector_factory
        self.tracker_factory = tracker_factory
        self.ocr_engine = ocr_engine or FakeOcrEngine([])
        runtime_provenance = provenance or measure_runtime_provenance(
            detector_factory(), tracker_factory(), self.ocr_engine
        )
        base_options = options or RunnerOptions()
        effective_max_frame_errors = (
            base_options.max_frame_errors
            if max_frame_errors is None
            else max_frame_errors
        )
        self.options = base_options.model_copy(
            update={
                "provenance": runtime_provenance,
                "max_frame_errors": effective_max_frame_errors,
            }
        )
        self.max_frame_errors = effective_max_frame_errors
        self.quality = quality or QualityOptions()
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
            self._validate_completed_artifacts(job)
            return job
        elif job.status is not JobStatus.RUNNING:
            raise RuntimeError(f"cannot run {job.status.value} full-match job")
        try:
            return self._run_active(job)
        except Exception as error:
            current = self.repository.get(job_id)
            if current.status is JobStatus.STOPPING:
                return self.repository.transition(job_id, JobStatus.STOPPED)
            if current.status is JobStatus.RUNNING:
                self.repository.transition(job_id, JobStatus.FAILED, error=str(error))
            raise

    def _run_active(self, job: JobRecord) -> JobRecord:
        job_id = job.id
        workspace = self.workspace_for(job_id)
        workspace.mkdir(parents=True, exist_ok=True)
        store = ManifestStore(workspace / "manifest.json")
        if store.exists():
            manifest = self._load_manifest(job, store)
            if manifest.job_id != job.id or manifest.source_uri != job.source_path:
                raise RuntimeError("manifest identity does not match the job")
            if manifest.options != self.options:
                raise RuntimeError("runner options differ from the immutable manifest")
            if not self._media_checkpoint_is_durable(
                manifest.proxy, manifest.proxy_sha256
            ):
                source = None
                try:
                    source, proxy = self._prepare_media(
                        job, workspace, rebuild_proxy=True
                    )
                    if sha256_file(source.path) != manifest.source_sha256:
                        raise RuntimeError("localized source identity changed")
                    self._validate_probe_identity(source, manifest.source)
                    manifest.proxy = proxy
                    manifest.proxy_sha256 = sha256_file(proxy.path)
                    self._invalidate_chunks_from(manifest, 0)
                    manifest.prepared_final_artifact = None
                    manifest.final_artifact = None
                    store.save(manifest)
                finally:
                    if source is not None:
                        self._delete_localized_source(source.path, workspace)
            else:
                self._cleanup_manifest_source(manifest, workspace)
        else:
            source = None
            try:
                source, proxy = self._prepare_media(job, workspace)
                self._raise_if_stopping(job_id)
                manifest = FullMatchManifest.create(
                    job_id=job.id,
                    source_uri=job.source_path,
                    options=self.options,
                    source=source,
                    proxy=proxy,
                    source_sha256=sha256_file(source.path),
                    proxy_sha256=sha256_file(proxy.path),
                )
                store.save(manifest)
                self.repository.save_job_metadata(
                    job_id, source=self._video_metadata(source)
                )
            finally:
                if source is not None:
                    self._delete_localized_source(source.path, workspace)
        repaired_manifest = False
        invalid_from: int | None = next(
            (
                index
                for index, chunk in enumerate(manifest.chunks)
                if chunk.status == "completed" and not self._chunk_is_durable(chunk)
            ),
            None,
        )
        if invalid_from is not None:
            self._invalidate_chunks_from(manifest, invalid_from)
            repaired_manifest = True
        if repaired_manifest:
            store.save(manifest)
        proxy_path = Path(manifest.proxy.path)
        durable_states = [
            chunk.consensus_state
            for chunk in manifest.chunks
            if chunk.status == "completed" and chunk.consensus_state is not None
        ]
        self._consensus = ScoreboardConsensus(
            job_id,
            producer=getattr(self.ocr_engine, "producer", "ocr.unknown"),
            producer_version=getattr(self.ocr_engine, "version", "unknown"),
            state=durable_states[-1] if durable_states else None,
        )
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
                        "consensus_state": result.consensus_state,
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
                current_progress = self.repository.get(job_id).progress
                if manifest.progress > current_progress:
                    self.repository.update_progress(job_id, manifest.progress)
            if manifest.prepared_final_artifact is not None:
                self._validate_final_artifact(
                    manifest, manifest.prepared_final_artifact, job_id
                )
                return self._commit_prepared_artifact(manifest, store)
            heat_map = self._aggregate_heat_map(manifest)
            heat_map_path = heat_map.write_png(workspace / "heat-map.png")
            output = self._finalize(
                manifest=manifest, proxy=proxy_path, workspace=workspace
            )
            output_probe = self._probe_final(output)
            manifest.prepared_final_artifact = FinalArtifact(
                path=str(output),
                sha256=sha256_file(output),
                heat_map_path=str(heat_map_path),
                heat_map_sha256=sha256_file(heat_map_path),
                probe=output_probe,
                completed_at=datetime.now(UTC),
            )
            store.save(manifest)
            return self._commit_prepared_artifact(manifest, store)
        except Exception as error:
            current = self.repository.get(job_id)
            if current.status is JobStatus.STOPPING:
                return self.repository.transition(job_id, JobStatus.STOPPED)
            if current.status is JobStatus.RUNNING:
                self.repository.transition(job_id, JobStatus.FAILED, error=str(error))
            raise

    def _commit_prepared_artifact(
        self, manifest: FullMatchManifest, store: ManifestStore
    ) -> JobRecord:
        artifact = manifest.prepared_final_artifact
        if artifact is None:
            raise RuntimeError("prepared final artifact is missing")
        finalized = self.repository.complete_or_stop(
            manifest.job_id,
            output_path=artifact.path,
            metrics={
                "full_match": "mvp",
                "chunks": len(manifest.chunks),
                "peak_observations": manifest.peak_observations,
            },
        )
        if finalized.status is not JobStatus.COMPLETED:
            delete_file_durable(artifact.path)
            delete_file_durable(artifact.heat_map_path)
            manifest.prepared_final_artifact = None
            store.save(manifest)
            return finalized
        manifest.final_artifact = artifact
        manifest.prepared_final_artifact = None
        store.save(manifest)
        self._persist_events(manifest)
        if artifact.probe is not None:
            self.repository.save_job_metadata(
                manifest.job_id, output=self._video_metadata(artifact.probe)
            )
        return finalized

    def _load_manifest(
        self, job: JobRecord, store: ManifestStore
    ) -> FullMatchManifest:
        payload = store.load_payload()
        if payload.get("schema_version") != 1:
            return FullMatchManifest.model_validate(payload)
        return self._migrate_v1_manifest(job, store, payload)

    def _migrate_v1_manifest(
        self,
        job: JobRecord,
        store: ManifestStore,
        payload: dict[str, object],
    ) -> FullMatchManifest:
        if payload.get("job_id") != job.id or payload.get("source_uri") != job.source_path:
            raise RuntimeError("manifest identity does not match the job")
        legacy_options = payload.get("options")
        if not isinstance(legacy_options, dict):
            raise ValueError("v1 manifest options are invalid")
        current_options = self.options.model_dump(mode="json")
        for key in ("chunk_ms", "overlap_ms", "ocr_interval_ms", "scoreboard_region"):
            if legacy_options.get(key) != current_options[key]:
                raise RuntimeError("runner options differ from the v1 manifest")
        source_expected = MediaProbe.model_validate(payload.get("source"))
        proxy_expected = MediaProbe.model_validate(payload.get("proxy"))
        source_path = Path(source_expected.path)
        proxy_path = Path(proxy_expected.path)
        source = None
        try:
            if source_path.is_file():
                try:
                    source = self._verified_v1_probe(source_path, source_expected)
                except (
                    OSError,
                    RuntimeError,
                    ValueError,
                    subprocess.SubprocessError,
                ):
                    self._delete_localized_source(source_path, store.path.parent)
            if source is None:
                source, proxy = self._prepare_media(
                    job, store.path.parent, rebuild_proxy=True
                )
            else:
                try:
                    proxy = self._verified_v1_probe(proxy_path, proxy_expected)
                except (
                    OSError,
                    RuntimeError,
                    ValueError,
                    subprocess.SubprocessError,
                ):
                    source, proxy = self._prepare_media(
                        job, store.path.parent, rebuild_proxy=True
                    )
            self._validate_v1_probe_identity(source, source_expected)
            self._validate_v1_probe_identity(proxy, proxy_expected)
            migrated_payload = dict(payload)
            migrated_payload.update(
                {
                    "schema_version": 2,
                    "options": current_options,
                    "source": source.model_dump(mode="json"),
                    "source_sha256": sha256_file(source.path),
                    "proxy": proxy.model_dump(mode="json"),
                    "proxy_sha256": sha256_file(proxy.path),
                    "prepared_final_artifact": None,
                }
            )
            manifest = FullMatchManifest.model_validate(migrated_payload)
            self._invalidate_chunks_from(manifest, 0)
            manifest.final_artifact = None
            store.save(manifest)
            return manifest
        finally:
            if source is not None:
                self._delete_localized_source(source.path, store.path.parent)

    def _prepare_media(
        self, job: JobRecord, workspace: Path, *, rebuild_proxy: bool = False
    ) -> tuple[MediaProbe, MediaProbe]:
        source_path: Path | None = None
        try:
            existing_sources = list(workspace.glob("source.*"))
            source_path = next(
                (path for path in existing_sources if path.name != "source.partial"),
                None,
            )
            if source_path is None:
                source_path = localize_s3_source(
                    self.object_store,
                    job.source_path,
                    bucket=self.bucket,
                    destination_dir=workspace,
                    cancelled=lambda: self._is_stopping(job.id),
                )
            self._raise_if_stopping(job.id)
            try:
                source = probe_media(
                    source_path, cancelled=lambda: self._is_stopping(job.id)
                )
            except (OSError, ValueError, subprocess.SubprocessError):
                delete_file_durable(source_path)
                source_path = localize_s3_source(
                    self.object_store,
                    job.source_path,
                    bucket=self.bucket,
                    destination_dir=workspace,
                    cancelled=lambda: self._is_stopping(job.id),
                )
                source = probe_media(
                    source_path, cancelled=lambda: self._is_stopping(job.id)
                )
            self._raise_if_stopping(job.id)
            validate_source_media(source)
            proxy_path = workspace / "proxy.mp4"
            if rebuild_proxy and proxy_path.exists():
                delete_file_durable(proxy_path)
            if not proxy_path.is_file():
                build_proxy(
                    source_path,
                    proxy_path,
                    cancelled=lambda: self._is_stopping(job.id),
                )
            self._raise_if_stopping(job.id)
            proxy = probe_media(
                proxy_path, cancelled=lambda: self._is_stopping(job.id)
            )
            return source, proxy
        except BaseException:
            if source_path is not None:
                self._delete_localized_source(source_path, workspace)
            raise

    def _cleanup_manifest_source(
        self, manifest: FullMatchManifest, workspace: Path
    ) -> None:
        source_path = Path(manifest.source.path)
        if not source_path.is_file():
            return
        if sha256_file(source_path) == manifest.source_sha256:
            self._verified_probe(source_path, manifest.source)
        self._delete_localized_source(source_path, workspace)

    @staticmethod
    def _delete_localized_source(path: str | Path, workspace: Path) -> None:
        target = Path(path).resolve()
        if target.parent != workspace.resolve() or not target.name.startswith("source."):
            raise RuntimeError("localized source path escaped the job workspace")
        delete_file_durable(target)

    def _probe_final(self, output: Path) -> MediaProbe | None:
        return probe_media(output)

    def _is_stopping(self, job_id: str) -> bool:
        return self.repository.get(job_id).status is JobStatus.STOPPING

    def _raise_if_stopping(self, job_id: str) -> None:
        if self._is_stopping(job_id):
            raise MediaCancelled("full-match stop requested")

    def _validate_completed_artifacts(self, job: JobRecord) -> None:
        manifest = self.status(job.id)
        artifact = manifest.final_artifact
        if artifact is None and manifest.prepared_final_artifact is not None:
            prepared = manifest.prepared_final_artifact
            if job.output_path != prepared.path:
                raise RuntimeError("completed output does not match prepared artifact")
            self._validate_final_artifact(manifest, prepared, job.id)
            manifest.final_artifact = prepared
            manifest.prepared_final_artifact = None
            ManifestStore(self.workspace_for(job.id) / "manifest.json").save(manifest)
            artifact = prepared
        if artifact is None or job.output_path != artifact.path:
            raise RuntimeError("completed full-match artifact manifest is missing")
        self._validate_final_artifact(manifest, artifact, job.id)
        if not self._media_checkpoint_is_durable(
            manifest.proxy, manifest.proxy_sha256
        ):
            raise RuntimeError("completed full-match proxy artifact integrity check failed")

    def _validate_final_artifact(
        self,
        manifest: FullMatchManifest,
        artifact: FinalArtifact,
        job_id: str,
    ) -> None:
        output = Path(artifact.path)
        heat_map = Path(artifact.heat_map_path)
        if (
            not output.is_file()
            or sha256_file(output) != artifact.sha256
            or not heat_map.is_file()
            or sha256_file(heat_map) != artifact.heat_map_sha256
        ):
            raise RuntimeError("completed full-match artifact integrity check failed")
        actual = self._probe_final(output)
        if actual is not None:
            if artifact.probe is not None:
                self._validate_probe_identity(actual, artifact.probe)
            self._validate_final(actual, manifest.proxy)
            self._validate_faststart(output)
            self._validate_full_decode(output, job_id)

    @staticmethod
    def _validate_probe_identity(actual: MediaProbe, expected: MediaProbe) -> None:
        if actual.model_dump(exclude={"path"}) != expected.model_dump(exclude={"path"}):
            raise RuntimeError("media probe identity changed")

    @staticmethod
    def _chunk_is_durable(chunk: ChunkRecord) -> bool:
        if chunk.output_path is None or chunk.sha256 is None:
            return False
        output = Path(chunk.output_path)
        return output.is_file() and sha256_file(output) == chunk.sha256

    def _media_checkpoint_is_durable(
        self, checkpoint: MediaProbe, expected_sha256: str
    ) -> bool:
        path = Path(checkpoint.path)
        if not path.is_file() or sha256_file(path) != expected_sha256:
            return False
        try:
            self._verified_probe(path, checkpoint)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            return False
        return True

    def _verified_probe(self, path: Path, expected: MediaProbe) -> MediaProbe:
        actual = probe_media(path)
        self._validate_probe_identity(actual, expected)
        return actual

    def _verified_v1_probe(self, path: Path, expected: MediaProbe) -> MediaProbe:
        actual = probe_media(path)
        self._validate_v1_probe_identity(actual, expected)
        return actual

    @staticmethod
    def _validate_v1_probe_identity(
        actual: MediaProbe, expected: MediaProbe
    ) -> None:
        legacy_fields = (
            "container",
            "video_codec",
            "width",
            "height",
            "fps",
            "frame_count",
            "duration_ms",
            "has_audio",
        )
        if any(
            getattr(actual, field) != getattr(expected, field)
            for field in legacy_fields
        ):
            raise RuntimeError("v1 media probe identity changed")

    @staticmethod
    def _invalidate_chunks_from(manifest: FullMatchManifest, start: int) -> None:
        for index, chunk in enumerate(manifest.chunks):
            if index < start:
                continue
            manifest.chunks[index] = chunk.model_copy(
                update={
                    "status": "pending",
                    "output_path": None,
                    "sha256": None,
                    "events": [],
                    "scoreboard": [],
                    "raw_ocr_evidence": [],
                    "consensus_state": None,
                    "heat_map_counts": None,
                    "peak_observations": 0,
                    "completed_at": None,
                }
            )

    def _process_chunk(
        self, *, job_id: str, proxy: Path, chunk: ChunkRecord
    ) -> ChunkResult:
        detector = self.detector_factory()
        tracker = self.tracker_factory()
        quality = self.quality
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
        engine = TemporalEventEngine(
            job_id,
            kick_speed_px_s=quality.kick_speed_px_s,
            proximity_px=quality.kick_proximity_px,
            min_contact_frames=quality.kick_min_contact_frames,
            min_ball_continuity=quality.kick_min_ball_continuity,
            cooldown_ms=quality.kick_cooldown_ms,
            max_confidence=quality.kick_max_confidence,
            max_ball_jump_px=round(
                quality.kick_max_jump_ratio * (width**2 + height**2) ** 0.5
            ),
        )
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
        trail_buffer = TrailBuffer(
            max_age_ms=quality.trail_max_age_ms,
            max_points=quality.trail_max_points,
            max_jump_ratio=quality.trail_max_jump_ratio,
        )
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
                rejected_detections: list[tuple[Detection, str]] = []
                try:
                    detections = detector.detect(frame, frame_index, timestamp_ms)
                    detections = apply_confidence_thresholds(
                        detections,
                        person_min_confidence=quality.person_min_confidence,
                        ball_min_confidence=quality.ball_min_confidence,
                    )
                    filtered = quality.playing_area.filter(
                        detections, frame_width=width, frame_height=height
                    )
                    detections = filtered.kept
                    rejected_detections = filtered.rejected
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
                            try:
                                raw = self.ocr_engine.read(
                                    frame, self.options.scoreboard_region
                                )
                            except Exception:
                                if self._consensus is not None:
                                    self._consensus.observe_missing(timestamp_ms)
                                raise
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
                            elif self._consensus is not None:
                                self._consensus.observe_missing(timestamp_ms)
                    for candidate in engine.drain_candidates():
                        if candidate.end_ms >= chunk.output_start_ms:
                            events.append(candidate)
                    trails = trail_buffer.update(
                        tracks,
                        frame_index=frame_index,
                        timestamp_ms=timestamp_ms,
                        frame_width=width,
                        frame_height=height,
                    )
                    active_events = active_events_at(
                        events[-50:],
                        timestamp_ms,
                        self.options.event_post_window_ms,
                    )
                    rendered = draw_overlay(
                        frame,
                        tracks,
                        active_events,
                        timestamp_ms=timestamp_ms,
                        trails=trails,
                        mode=quality.overlay_mode,
                        active_ceiling=quality.active_track_ceiling,
                        playing_area=quality.playing_area.polygon or None,
                        rejected=rejected_detections,
                        banner_duration_ms=quality.banner_duration_ms,
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
            run_media_command(
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
                    self.options.encoder_preset,
                    "-pix_fmt",
                    "yuv420p",
                    str(temporary),
                ],
                cancelled=lambda: self._is_stopping(job_id),
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
            consensus_state=self._consensus.snapshot() if self._consensus else None,
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
            run_media_command(
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
                cancelled=lambda: self._is_stopping(manifest.job_id),
            )
            run_media_command(
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
                    manifest.options.encoder_preset,
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(temporary),
                ],
                cancelled=lambda: self._is_stopping(manifest.job_id),
            )
            actual = probe_media(temporary)
            self._validate_final(actual, manifest.proxy)
            self._validate_faststart(temporary)
            self._validate_full_decode(temporary, manifest.job_id)
            temporary.replace(output)
        finally:
            video_only.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
        return output

    @staticmethod
    def _validate_final(actual: MediaProbe, expected: MediaProbe) -> None:
        if not {"mov", "mp4"} & set(actual.container.split(",")):
            raise RuntimeError("annotated video is not an MP4 container")
        if actual.video_codec != "h264":
            raise RuntimeError("annotated video is not H.264")
        if actual.pixel_format != "yuv420p":
            raise RuntimeError("annotated video is not yuv420p")
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
        if expected.has_audio and actual.audio_codec != "aac":
            raise RuntimeError("annotated video audio is not AAC")
        if (
            expected.has_audio
            and actual.audio_start_ms is not None
            and abs(actual.audio_start_ms - actual.video_start_ms) > 100
        ):
            raise RuntimeError("annotated audio/video start times diverge")
        if (
            expected.has_audio
            and actual.audio_duration_ms is not None
            and abs(actual.audio_duration_ms - actual.duration_ms) > 1_000
        ):
            raise RuntimeError("annotated audio/video durations diverge")

    @staticmethod
    def _validate_faststart(path: Path) -> None:
        with path.open("rb") as source:
            header = source.read(2 * 1024 * 1024)
        moov = header.find(b"moov")
        mdat = header.find(b"mdat")
        if moov < 0 or mdat < 0 or moov > mdat:
            raise RuntimeError("annotated MP4 is not faststart/browser compatible")

    def _validate_full_decode(self, path: Path, job_id: str) -> None:
        validate_media_decode(
            path,
            cancelled=lambda: self._is_stopping(job_id),
        )

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
