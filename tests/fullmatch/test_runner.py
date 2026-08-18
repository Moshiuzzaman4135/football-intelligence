import json
import subprocess
from pathlib import Path

import pytest

import football_intelligence.fullmatch.runner as runner_module
from football_intelligence.domain import (
    BoundingBox,
    EventEvidence,
    FootballEvent,
    JobStatus,
    ScoreboardObservation,
    ScoreboardRegion,
    TrackObservation,
)
from football_intelligence.fullmatch.heatmap import ScreenSpaceHeatMap
from football_intelligence.fullmatch.manifest import ChunkRecord, RunnerOptions
from football_intelligence.fullmatch.media import MediaCancelled, MediaProbe
from football_intelligence.fullmatch.ocr import TesseractCliOcrEngine
from football_intelligence.fullmatch.runner import (
    BoundedTrails,
    ChunkResult,
    FullMatchRunner,
    RuntimeProvenance,
    active_events_at,
    namespace_track_id,
)
from football_intelligence.object_store import InMemoryObjectStore
from football_intelligence.storage import JobRepository


class SimulatedProcessDeath(BaseException):
    pass


class FakeRunner(FullMatchRunner):
    def __init__(self, *args, die_on_chunk: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.die_on_chunk = die_on_chunk
        self.processed: list[int] = []

    def _prepare_media(self, job, workspace, *, rebuild_proxy=False):
        del rebuild_proxy
        proxy = workspace / "proxy.mp4"
        proxy.write_bytes(b"bounded proxy")
        source = workspace / "source.mp4"
        source.write_bytes(b"bounded source")
        metadata = MediaProbe(
            path=str(proxy),
            container="mov,mp4",
            video_codec="h264",
            width=320,
            height=180,
            fps=25,
            frame_count=6_250,
            duration_ms=250_000,
            has_audio=True,
        )
        return metadata.model_copy(update={"path": str(source)}), metadata

    def _process_chunk(self, *, job_id: str, proxy: Path, chunk: ChunkRecord):
        del proxy
        self.processed.append(chunk.index)
        if chunk.index == self.die_on_chunk:
            raise SimulatedProcessDeath
        output = self.workspace_for(job_id) / f"chunk-{chunk.index:04d}.mp4"
        output.write_bytes(f"chunk {chunk.index}".encode())
        event = FootballEvent(
            id=f"event-{chunk.index}",
            job_id=job_id,
            event_type="kick_candidate",
            start_ms=chunk.output_start_ms,
            end_ms=chunk.output_start_ms,
            description="bounded candidate",
            confidence=0.8,
            evidence=[EventEvidence(kind="test", value=True, confidence=0.8)],
            source=["test"],
            track_ids=[namespace_track_id(chunk.index, 7)],
        )
        scoreboard = ScoreboardObservation(
            timestamp_ms=chunk.output_start_ms,
            match_clock_ms=chunk.output_start_ms,
            period=1,
            home_team="AAA",
            away_team="BBB",
            home_score=0,
            away_score=0,
            confidence=0.9,
            region=ScoreboardRegion(x=0, y=0, width=1, height=0.2),
            frame_index=chunk.output_start_ms // 40,
        )
        heatmap = ScreenSpaceHeatMap()
        return ChunkResult(
            path=output,
            events=[event],
            scoreboard=[scoreboard],
            heat_map_counts=heatmap.to_counts(),
            peak_observations=3,
        )

    def _finalize(self, *, manifest, proxy, workspace):
        del manifest, proxy
        output = workspace / "annotated.mp4"
        output.write_bytes(b"final h264 fixture")
        return output

    def _probe_final(self, output):
        del output
        return None

    def _media_checkpoint_is_durable(self, checkpoint, expected_sha256):
        from football_intelligence.fullmatch.media import sha256_file

        path = Path(checkpoint.path)
        return path.is_file() and sha256_file(path) == expected_sha256

    def _verified_probe(self, path, expected):
        return expected.model_copy(update={"path": str(path)})

    def _verified_v1_probe(self, path, expected):
        return self._verified_probe(path, expected)


def test_crash_after_chunk_one_resumes_without_reprocessing_or_raw_sql(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    first = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
        die_on_chunk=1,
    )

    with pytest.raises(SimulatedProcessDeath):
        first.run(job.id)

    assert first.processed == [0, 1]
    assert repository.get(job.id).status is JobStatus.RUNNING
    second = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )

    completed = second.run(job.id)

    assert completed.status is JobStatus.COMPLETED
    assert second.processed == [1, 2]
    assert repository.get_tracks(job.id) == []
    assert [event.id for event in repository.get_events(job.id)] == [
        "event-0",
        "event-1",
        "event-2",
    ]
    manifest = second.status(job.id)
    assert manifest.progress == 100
    assert all(chunk.status == "completed" for chunk in manifest.chunks)
    assert manifest.peak_observations == 3


def test_real_v1_running_manifest_migrates_and_conservatively_reprocesses_chunks(
    tmp_path: Path,
):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    first = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
        die_on_chunk=1,
    )
    with pytest.raises(SimulatedProcessDeath):
        first.run(job.id)

    manifest_path = first.workspace_for(job.id) / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    payload.pop("source_sha256")
    payload.pop("proxy_sha256")
    payload.pop("prepared_final_artifact")
    payload["options"] = {
        key: payload["options"][key]
        for key in ("chunk_ms", "overlap_ms", "ocr_interval_ms", "scoreboard_region")
    }
    for probe_name in ("source", "proxy"):
        payload[probe_name] = {
            key: value
            for key, value in payload[probe_name].items()
            if key
            in {
                "path",
                "container",
                "video_codec",
                "width",
                "height",
                "fps",
                "frame_count",
                "duration_ms",
                "has_audio",
            }
        }
    for chunk in payload["chunks"]:
        chunk.pop("consensus_state")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    Path(payload["source"]["path"]).write_bytes(b"bounded source")

    resumed = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )
    completed = resumed.run(job.id)

    assert completed.status is JobStatus.COMPLETED
    assert resumed.processed == [0, 1, 2]
    migrated = resumed.status(job.id)
    assert migrated.schema_version == 2
    assert migrated.options == resumed.options
    assert migrated.source_sha256 != "0" * 64
    assert migrated.proxy_sha256 != "0" * 64
    assert not Path(migrated.source.path).exists()


def test_v1_probe_verification_ignores_fields_that_did_not_exist_in_v1(
    tmp_path: Path, monkeypatch
):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    runner = FullMatchRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )
    legacy = MediaProbe(
        path=str(tmp_path / "source.mov"),
        container="mov,mp4",
        video_codec="h264",
        width=320,
        height=180,
        fps=25,
        frame_count=250,
        duration_ms=10_000,
        has_audio=True,
    )
    current = legacy.model_copy(
        update={
            "pixel_format": "yuv420p",
            "audio_codec": "aac",
            "audio_start_ms": 20,
            "audio_duration_ms": 9_980,
        }
    )
    monkeypatch.setattr(runner_module, "probe_media", lambda path: current)

    assert runner._verified_v1_probe(Path(legacy.path), legacy) == current


def test_resume_reprocesses_completed_chunk_when_checksum_artifact_is_missing(
    tmp_path: Path,
):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    first = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
        die_on_chunk=1,
    )
    with pytest.raises(SimulatedProcessDeath):
        first.run(job.id)
    (first.workspace_for(job.id) / "chunk-0000.mp4").unlink()
    second = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )

    second.run(job.id)

    assert second.processed == [0, 1, 2]


def test_invalid_chunk_invalidates_all_state_dependent_successors(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    first = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
        die_on_chunk=2,
    )
    with pytest.raises(SimulatedProcessDeath):
        first.run(job.id)
    (first.workspace_for(job.id) / "chunk-0000.mp4").write_bytes(b"corrupt")
    second = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )

    second.run(job.id)

    assert second.processed == [0, 1, 2]


@pytest.mark.parametrize("failure", [OSError("s3"), ValueError("probe"), RuntimeError("proxy")])
def test_controlled_preparation_failure_after_start_marks_job_failed(
    tmp_path: Path, failure: Exception
):
    repository = JobRepository(tmp_path / f"{type(failure).__name__}.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )

    class FailingPreparation(FakeRunner):
        def _prepare_media(self, job, workspace, *, rebuild_proxy=False):
            del rebuild_proxy
            del job, workspace
            raise failure

    runner = FailingPreparation(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )

    with pytest.raises(type(failure), match=str(failure) or None):
        runner.run(job.id)

    failed = repository.get(job.id)
    assert failed.status is JobStatus.FAILED
    assert failed.error == str(failure)


def test_process_death_during_preparation_leaves_job_resumable(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )

    class DyingPreparation(FakeRunner):
        def _prepare_media(self, job, workspace, *, rebuild_proxy=False):
            del job, workspace, rebuild_proxy
            raise SimulatedProcessDeath

    first = DyingPreparation(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )
    with pytest.raises(SimulatedProcessDeath):
        first.run(job.id)
    assert repository.get(job.id).status is JobStatus.RUNNING

    resumed = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    ).run(job.id)
    assert resumed.status is JobStatus.COMPLETED


def test_real_stream_and_probe_failures_are_lifecycle_guarded(tmp_path: Path):
    class FailingStreamStore(InMemoryObjectStore):
        def iter_object(self, object_key, chunk_size=1024 * 1024):
            del object_key, chunk_size
            raise OSError("S3 stream failed")
            yield b""  # pragma: no cover

    for job_id, store, expected in (
        ("s3", FailingStreamStore(), "S3 stream failed"),
        ("probe", InMemoryObjectStore(), "ffprobe"),
    ):
        repository = JobRepository(tmp_path / f"{job_id}.sqlite3")
        key = f"uploads/{job_id}.mp4"
        if job_id == "probe":
            upload = store.create_multipart(key, "video/mp4")
            part = store.upload_part(upload, key, 1, b"not media")
            store.complete_multipart(upload, key, [part])
        job = repository.create_with_id(job_id, f"s3://football-media/{key}", f"{job_id}.mp4")
        runner = FullMatchRunner(
            repository=repository,
            object_store=store,
            bucket="football-media",
            data_root=tmp_path / job_id,
        )

        with pytest.raises((OSError, subprocess.SubprocessError)):
            runner.run(job.id)

        assert repository.get(job.id).status is JobStatus.FAILED
        assert expected.lower() in repository.get(job.id).error.lower()


def test_real_proxy_failure_is_lifecycle_guarded(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=green:s=160x90:r=1:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    store = InMemoryObjectStore()
    key = "uploads/proxy.mp4"
    upload = store.create_multipart(key, "video/mp4")
    part = store.upload_part(upload, key, 1, source.read_bytes())
    store.complete_multipart(upload, key, [part])
    repository = JobRepository(tmp_path / "proxy.sqlite3")
    job = repository.create_with_id("job", f"s3://football-media/{key}", "proxy.mp4")

    def fail_proxy(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("proxy encoder failed")

    monkeypatch.setattr("football_intelligence.fullmatch.runner.build_proxy", fail_proxy)
    runner = FullMatchRunner(
        repository=repository,
        object_store=store,
        bucket="football-media",
        data_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="proxy encoder failed"):
        runner.run(job.id)

    assert repository.get(job.id).status is JobStatus.FAILED
    assert not (runner.workspace_for(job.id) / "source.mp4").exists()


@pytest.mark.parametrize("manifest_body", ["{corrupt", '{"job_id":"another"}'])
def test_corrupt_or_mismatched_manifest_marks_running_job_failed(
    tmp_path: Path, manifest_body: str
):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    repository.transition(job.id, JobStatus.RUNNING)
    workspace = tmp_path / "fullmatch" / job.id
    workspace.mkdir(parents=True)
    (workspace / "manifest.json").write_text(manifest_body, encoding="utf-8")
    runner = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )

    with pytest.raises((ValueError, RuntimeError)):
        runner.run(job.id)

    assert repository.get(job.id).status is JobStatus.FAILED


def test_completed_fast_return_validates_final_and_heat_map_hashes(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    runner = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )
    runner.run(job.id)
    runner.heat_map_path(job.id).write_bytes(b"corrupt")

    with pytest.raises(RuntimeError, match="artifact"):
        runner.run(job.id)


def test_completed_fast_return_rejects_corrupt_proxy_checkpoint(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    runner = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )
    runner.run(job.id)
    Path(runner.status(job.id).proxy.path).write_bytes(b"corrupt")

    with pytest.raises(RuntimeError, match="proxy"):
        runner.run(job.id)


def test_prepared_final_artifact_recovers_crash_before_database_completion(
    tmp_path: Path,
):
    class DieBeforeCommitRepository(JobRepository):
        fail_once = True

        def complete_or_stop(self, job_id, *, output_path, metrics):
            if self.fail_once:
                self.fail_once = False
                raise SimulatedProcessDeath
            return super().complete_or_stop(
                job_id, output_path=output_path, metrics=metrics
            )

    repository = DieBeforeCommitRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    first = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )
    with pytest.raises(SimulatedProcessDeath):
        first.run(job.id)

    prepared = first.status(job.id).prepared_final_artifact
    assert repository.get(job.id).status is JobStatus.RUNNING
    assert prepared is not None
    assert first.status(job.id).final_artifact is None

    class MustNotEncodeAgain(FakeRunner):
        def _finalize(self, **kwargs):
            raise AssertionError("durable prepared artifact must be reused")

    resumed = MustNotEncodeAgain(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )
    completed = resumed.run(job.id)

    assert completed.status is JobStatus.COMPLETED
    manifest = resumed.status(job.id)
    assert manifest.prepared_final_artifact is None
    assert manifest.final_artifact == prepared


def test_completed_crash_window_promotes_only_precommitted_artifact_bytes(
    tmp_path: Path,
):
    class CommitThenDieRepository(JobRepository):
        def complete_or_stop(self, job_id, *, output_path, metrics):
            super().complete_or_stop(job_id, output_path=output_path, metrics=metrics)
            raise SimulatedProcessDeath from None

    repository = CommitThenDieRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    runner = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )
    with pytest.raises(SimulatedProcessDeath):
        runner.run(job.id)

    manifest = runner.status(job.id)
    assert repository.get(job.id).status is JobStatus.COMPLETED
    assert manifest.prepared_final_artifact is not None
    assert manifest.final_artifact is None
    Path(repository.get(job.id).output_path).write_bytes(b"replacement")

    with pytest.raises(RuntimeError, match="integrity"):
        runner.run(job.id)
    recovered = runner.status(job.id)
    assert recovered.final_artifact is None
    assert recovered.prepared_final_artifact == manifest.prepared_final_artifact


def test_runtime_provenance_is_immutable_and_output_affecting(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    provenance = RuntimeProvenance(
        detector="test.detector",
        detector_model="weights.pt",
        detector_device="cpu",
        detector_framework="test-framework",
        detector_version="1.2.3",
        detector_config={"confidence": "0.42"},
        tracker="test.tracker",
        tracker_config={"iou": "0.25"},
        ocr_engine="test.ocr",
        ocr_model="eng",
        ocr_version="5.5.0",
        ocr_model_sha256="7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    )
    runner = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
        provenance=provenance,
        die_on_chunk=1,
    )
    with pytest.raises(SimulatedProcessDeath):
        runner.run(job.id)

    manifest = runner.status(job.id)

    assert manifest.options.provenance == provenance
    assert manifest.options.max_frame_errors == 10
    assert manifest.options.output_video_codec == "h264"
    assert manifest.options.output_pixel_format == "yuv420p"
    assert manifest.source_sha256 != "0" * 64
    assert manifest.proxy_sha256 != "0" * 64
    assert not Path(manifest.source.path).exists()

    changed = provenance.model_copy(update={"detector_model": "other-weights.pt"})
    resumed = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
        provenance=changed,
    )
    with pytest.raises(RuntimeError, match="runner options"):
        resumed.run(job.id)
    assert repository.get(job.id).status is JobStatus.FAILED


def test_measured_ocr_executable_and_model_changes_reject_resume(tmp_path: Path):
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    model = tessdata / "eng.traineddata"
    executable = tmp_path / "tesseract"

    def configure(version: str, model_bytes: bytes) -> TesseractCliOcrEngine:
        executable.write_text(
            f"#!/bin/sh\nprintf 'tesseract {version}\\n'\n", encoding="utf-8"
        )
        executable.chmod(0o755)
        model.write_bytes(model_bytes)
        return TesseractCliOcrEngine(tessdata, executable=str(executable))

    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    first = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
        ocr_engine=configure("9.1.0", b"model-v1"),
        die_on_chunk=1,
    )
    with pytest.raises(SimulatedProcessDeath):
        first.run(job.id)

    resumed = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
        ocr_engine=configure("9.2.0", b"model-v2"),
    )
    with pytest.raises(RuntimeError, match="runner options"):
        resumed.run(job.id)


def test_explicit_runner_options_preserve_frame_error_policy(tmp_path: Path):
    runner = FakeRunner(
        repository=JobRepository(tmp_path / "jobs.sqlite3"),
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
        options=RunnerOptions(max_frame_errors=37),
    )

    assert runner.options.max_frame_errors == 37
    assert runner.max_frame_errors == 37


def test_corrupt_proxy_is_rebuilt_and_invalidates_every_chunk(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    first = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
        die_on_chunk=1,
    )
    with pytest.raises(SimulatedProcessDeath):
        first.run(job.id)
    Path(first.status(job.id).proxy.path).write_bytes(b"corrupt")
    second = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )

    second.run(job.id)

    assert second.processed == [0, 1, 2]


@pytest.mark.parametrize("failure", [RuntimeError("metadata"), SimulatedProcessDeath()])
def test_source_cleanup_survives_metadata_failure_or_process_death(
    tmp_path: Path, failure: BaseException
):
    class FailingMetadataRepository(JobRepository):
        def save_job_metadata(self, job_id, *, source=None, output=None):
            del job_id, source, output
            raise failure

    repository = FailingMetadataRepository(
        tmp_path / f"{type(failure).__name__}.sqlite3"
    )
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    runner = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )

    with pytest.raises(type(failure), match=str(failure) or None):
        runner.run(job.id)

    assert not (runner.workspace_for(job.id) / "source.mp4").exists()


def test_resume_removes_validated_localized_source_leftover(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )
    first = FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
        die_on_chunk=1,
    )
    with pytest.raises(SimulatedProcessDeath):
        first.run(job.id)
    source_path = Path(first.status(job.id).source.path)
    source_path.write_bytes(b"bounded source")

    FakeRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    ).run(job.id)

    assert not source_path.exists()


def test_track_namespace_is_deterministic_and_chunk_local():
    assert namespace_track_id(0, 7) == 7
    assert namespace_track_id(1, 7) == 1_000_007
    assert namespace_track_id(1, 7) != namespace_track_id(2, 7)


def test_trails_evict_inactive_tracks_and_enforce_global_bound():
    trails = BoundedTrails(max_points=3, max_inactive_frames=2, max_tracks=2)

    def track(track_id: int, frame: int) -> TrackObservation:
        return TrackObservation(
            track_id=track_id,
            object_class="player",
            bbox=BoundingBox(x1=1, y1=1, x2=3, y2=5),
            confidence=0.9,
            timestamp_ms=frame * 40,
            frame_index=frame,
        )

    trails.update([track(1, 0), track(2, 0)], 0)
    current = trails.update([track(3, 1)], 1)
    assert len(current) == 2 and 3 in current
    current = trails.update([], 4)
    assert current == {}


def test_overlay_events_are_limited_to_active_window():
    def event(event_id: str, start_ms: int, end_ms: int) -> FootballEvent:
        return FootballEvent(
            id=event_id,
            job_id="job-1",
            event_type="candidate",
            start_ms=start_ms,
            end_ms=end_ms,
            description="candidate",
            confidence=0.8,
            evidence=[EventEvidence(kind="test", value=True, confidence=0.8)],
            source=["test"],
        )

    events = [
        event("old", 0, 100),
        event("active", 1_000, 1_100),
        event("future", 9_000, 9_100),
    ]

    assert [item.id for item in active_events_at(events, 1_500)] == ["active"]
    assert active_events_at(events, 8_000) == []


def test_stop_between_chunks_does_not_finalize(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )

    class StopAfterFirst(FakeRunner):
        def _process_chunk(self, **kwargs):
            result = super()._process_chunk(**kwargs)
            if kwargs["chunk"].index == 0:
                self.repository.transition(job.id, JobStatus.STOPPING)
            return result

    runner = StopAfterFirst(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )

    stopped = runner.run(job.id)

    assert stopped.status is JobStatus.STOPPED
    assert runner.processed == [0]
    assert not (runner.workspace_for(job.id) / "annotated.mp4").exists()


def test_stop_during_preparation_stops_without_publishing_manifest(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )

    class StopDuringPreparation(FakeRunner):
        def _prepare_media(self, job, workspace, *, rebuild_proxy=False):
            del rebuild_proxy
            result = super()._prepare_media(job, workspace)
            self.repository.transition(job.id, JobStatus.STOPPING)
            return result

    runner = StopDuringPreparation(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )

    stopped = runner.run(job.id)

    assert stopped.status is JobStatus.STOPPED
    assert not (runner.workspace_for(job.id) / "manifest.json").exists()
    assert not (runner.workspace_for(job.id) / "source.mp4").exists()


def test_stop_during_s3_localization_removes_partial_and_stops(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )

    class StopDuringStream(InMemoryObjectStore):
        def iter_object(self, object_key, chunk_size=1024 * 1024):
            del object_key, chunk_size
            repository.transition(job.id, JobStatus.STOPPING)
            yield b"not written"

    runner = FullMatchRunner(
        repository=repository,
        object_store=StopDuringStream(),
        bucket="football-media",
        data_root=tmp_path,
    )

    stopped = runner.run(job.id)

    assert stopped.status is JobStatus.STOPPED
    workspace = runner.workspace_for(job.id)
    assert not (workspace / "source.partial").exists()
    assert not (workspace / "manifest.json").exists()


def test_stop_during_proxy_encode_does_not_publish_manifest(tmp_path: Path):
    source = tmp_path / "fixture.mp4"
    source.write_bytes(b"localized source")
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )

    class ProxyBarrierRunner(FullMatchRunner):
        def _prepare_media(self, job, workspace, *, rebuild_proxy=False):
            del rebuild_proxy
            localized = workspace / "source.mp4"
            localized.write_bytes(source.read_bytes())
            repository.transition(job.id, JobStatus.STOPPING)
            raise MediaCancelled("proxy encode cancelled")

    runner = ProxyBarrierRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )

    stopped = runner.run(job.id)

    assert stopped.status is JobStatus.STOPPED
    assert not (runner.workspace_for(job.id) / "manifest.json").exists()


def test_stop_during_final_encode_does_not_publish_final_artifact(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "job-1", "s3://football-media/uploads/video.mp4", "video.mp4"
    )

    class StopDuringFinal(FakeRunner):
        def _finalize(self, **kwargs):
            output = super()._finalize(**kwargs)
            self.repository.transition(job.id, JobStatus.STOPPING)
            return output

    runner = StopDuringFinal(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="football-media",
        data_root=tmp_path,
    )

    stopped = runner.run(job.id)

    assert stopped.status is JobStatus.STOPPED
    assert runner.status(job.id).final_artifact is None
    assert not (runner.workspace_for(job.id) / "annotated.mp4").exists()
