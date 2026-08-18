import hashlib
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient

from football_intelligence.api import create_app
from football_intelligence.domain import JobStatus
from football_intelligence.fullmatch.manifest import (
    FinalArtifact,
    FullMatchManifest,
    ManifestStore,
    RunnerOptions,
)
from football_intelligence.fullmatch.media import MediaProbe
from football_intelligence.fullmatch.runner import FullMatchRunner
from football_intelligence.object_store import InMemoryObjectStore
from football_intelligence.storage import InvalidJobTransition, JobRepository


class FakeFullMatchRunner:
    def __init__(self, repository, data_root: Path, gate: Event | None = None):
        self.repository = repository
        self.data_root = data_root
        self.gate = gate
        self.calls: list[str] = []

    def run(self, job_id: str):
        self.calls.append(job_id)
        if self.gate is not None:
            self.gate.wait(timeout=5)
        return self.repository.get(job_id)

    def status(self, job_id: str):
        path = self.data_root / "fullmatch" / job_id / "proxy.mp4"
        probe = MediaProbe(
            path=str(path),
            container="mov,mp4",
            video_codec="h264",
            width=320,
            height=180,
            fps=25,
            frame_count=25,
            duration_ms=1_000,
            has_audio=False,
        )
        return FullMatchManifest.create(
            job_id=job_id,
            source_uri=self.repository.get(job_id).source_path,
            options=RunnerOptions(),
            source=probe,
            proxy=probe,
        )

    def scoreboard(self, job_id: str):
        self.repository.get(job_id)
        return []

    def heat_map_path(self, job_id: str):
        path = self.data_root / "fullmatch" / job_id / "heat-map.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png fixture")
        return path


def test_full_match_run_is_idempotent_and_has_dedicated_single_slot(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    first = repository.create_with_id("one", "s3://bucket/one.mp4", "one.mp4")
    second = repository.create_with_id("two", "s3://bucket/two.mp4", "two.mp4")
    gate = Event()
    runner = FakeFullMatchRunner(repository, tmp_path, gate)
    app = create_app(
        repository=repository,
        data_root=tmp_path,
        pipeline_factory=lambda: None,
        full_match_runner_factory=lambda: runner,
    )

    with TestClient(app) as client:
        accepted = client.post(f"/jobs/{first.id}/full-match/run")
        replay = client.post(f"/jobs/{first.id}/full-match/run")
        full = client.post(f"/jobs/{second.id}/full-match/run")
        gate.set()

    assert accepted.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["status"] == "running"
    assert full.status_code == 429
    assert runner.calls == [first.id]


def test_full_match_read_apis_and_annotated_video_range(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id("one", "s3://bucket/one.mp4", "one.mp4")
    runner = FakeFullMatchRunner(repository, tmp_path)
    output = tmp_path / "annotated.mp4"
    output.write_bytes(b"0123456789")
    repository.transition(job.id, JobStatus.RUNNING)
    repository.complete_or_stop(job.id, output_path=str(output), metrics={})
    app = create_app(
        repository=repository,
        data_root=tmp_path,
        pipeline_factory=lambda: None,
        full_match_runner_factory=lambda: runner,
    )

    with TestClient(app) as client:
        status = client.get(f"/jobs/{job.id}/full-match/status")
        scoreboard = client.get(f"/jobs/{job.id}/scoreboard")
        debug = client.get(f"/jobs/{job.id}/full-match/debug")
        heat_map = client.get(f"/jobs/{job.id}/heat-map")
        video = client.get(
            f"/jobs/{job.id}/annotated-video", headers={"Range": "bytes=2-5"}
        )
        replay = client.post(f"/jobs/{job.id}/full-match/run")

    assert status.status_code == 200
    assert status.json()["job_status"] == "completed"
    assert status.json()["manifest"]["job_id"] == job.id
    assert scoreboard.json() == []
    assert debug.status_code == 200
    assert debug.json()["job_id"] == job.id
    assert "chunks" in debug.json()
    assert "peak_observations" in debug.json()
    assert "options" in debug.json()
    assert heat_map.status_code == 200
    assert video.status_code == 206
    assert video.content == b"2345"
    assert replay.status_code == 200
    assert replay.json()["status"] == "completed"
    assert runner.calls == [job.id]


def test_running_job_is_resubmitted_after_api_process_restart(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id("one", "s3://bucket/one.mp4", "one.mp4")
    repository.transition(job.id, JobStatus.RUNNING)
    runner = FakeFullMatchRunner(repository, tmp_path)
    app = create_app(
        repository=repository,
        data_root=tmp_path,
        pipeline_factory=lambda: None,
        full_match_runner_factory=lambda: runner,
    )

    with TestClient(app) as client:
        resumed = client.post(f"/jobs/{job.id}/full-match/run")

    assert resumed.status_code == 202
    assert resumed.json()["status"] == "running"
    assert runner.calls == [job.id]


def test_completed_api_replay_promotes_precommitted_crash_artifact(
    tmp_path: Path,
):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id("one", "s3://bucket/one.mp4", "one.mp4")
    workspace = tmp_path / "fullmatch" / job.id
    workspace.mkdir(parents=True)
    proxy = workspace / "proxy.mp4"
    output = workspace / "annotated.mp4"
    heat_map = workspace / "heat-map.png"
    proxy.write_bytes(b"proxy")
    output.write_bytes(b"precommitted output")
    heat_map.write_bytes(b"precommitted heat map")
    probe = MediaProbe(
        path=str(proxy),
        container="mov,mp4",
        video_codec="h264",
        width=320,
        height=180,
        fps=25,
        frame_count=25,
        duration_ms=1_000,
        has_audio=False,
    )
    manifest = FullMatchManifest.create(
        job_id=job.id,
        source_uri=job.source_path,
        options=RunnerOptions(),
        source=probe.model_copy(update={"path": str(workspace / "source.mp4")}),
        proxy=probe,
    )
    manifest.prepared_final_artifact = FinalArtifact(
        path=str(output),
        sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        heat_map_path=str(heat_map),
        heat_map_sha256=hashlib.sha256(heat_map.read_bytes()).hexdigest(),
        completed_at=datetime.now(UTC),
    )
    ManifestStore(workspace / "manifest.json").save(manifest)
    repository.transition(job.id, JobStatus.RUNNING)
    repository.complete_or_stop(job.id, output_path=str(output), metrics={})

    class RecoveryRunner(FullMatchRunner):
        def _probe_final(self, output):
            del output
            return None

        def _media_checkpoint_is_durable(self, checkpoint, expected_sha256):
            del checkpoint, expected_sha256
            return True

    runner = RecoveryRunner(
        repository=repository,
        object_store=InMemoryObjectStore(),
        bucket="bucket",
        data_root=tmp_path,
    )
    app = create_app(
        repository=repository,
        data_root=tmp_path,
        pipeline_factory=lambda: None,
        full_match_runner_factory=lambda: runner,
    )

    with TestClient(app) as client:
        replay = client.post(f"/jobs/{job.id}/full-match/run")

    assert replay.status_code == 200
    recovered = ManifestStore(workspace / "manifest.json").load()
    assert recovered.prepared_final_artifact is None
    assert recovered.final_artifact == manifest.prepared_final_artifact


def test_full_match_start_stop_cas_race_maps_to_conflict_and_releases_slot(
    tmp_path: Path,
):
    class StopWinsRepository(JobRepository):
        stop_on_start = True

        def transition(self, job_id, target, **kwargs):
            if target is JobStatus.RUNNING and self.stop_on_start:
                self.stop_on_start = False
                super().transition(job_id, JobStatus.STOPPED)
                raise InvalidJobTransition("stop won")
            return super().transition(job_id, target, **kwargs)

    repository = StopWinsRepository(tmp_path / "jobs.sqlite3")
    stopped = repository.create_with_id("one", "s3://bucket/one.mp4", "one.mp4")
    next_job = repository.create_with_id("two", "s3://bucket/two.mp4", "two.mp4")
    runner = FakeFullMatchRunner(repository, tmp_path)
    app = create_app(
        repository=repository,
        data_root=tmp_path,
        pipeline_factory=lambda: None,
        full_match_runner_factory=lambda: runner,
    )

    with TestClient(app) as client:
        conflict = client.post(f"/jobs/{stopped.id}/full-match/run")
        accepted = client.post(f"/jobs/{next_job.id}/full-match/run")

    assert conflict.status_code == 409
    assert accepted.status_code == 202
