from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient

from football_intelligence.api import create_app
from football_intelligence.domain import JobStatus
from football_intelligence.storage import JobRepository


class ImmediatePipeline:
    def __init__(self, repository, completed):
        self.repository = repository
        self.completed = completed

    def run(self, job_id):
        assert self.repository.get(job_id).status is JobStatus.RUNNING
        self.repository.transition(job_id, JobStatus.COMPLETED)
        self.completed.set()


class BlockingPipeline:
    def __init__(self, repository, entered, release):
        self.repository = repository
        self.entered = entered
        self.release = release

    def run(self, job_id):
        assert self.repository.get(job_id).status is JobStatus.RUNNING
        self.entered.set()
        assert self.release.wait(timeout=2)
        self.repository.transition(job_id, JobStatus.COMPLETED)


def test_health_and_upload_create_safe_job(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.db")
    app = create_app(repository=repository, data_root=tmp_path)

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post(
            "/jobs/upload",
            files={"file": ("../../match.mp4", b"video-bytes", "video/mp4")},
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "created"
    assert payload["original_filename"] == "match.mp4"
    assert Path(payload["source_path"]).parent == tmp_path / "uploads"
    assert Path(payload["source_path"]).read_bytes() == b"video-bytes"


def test_upload_rejects_unsupported_extension(tmp_path: Path):
    app = create_app(repository=JobRepository(tmp_path / "jobs.db"), data_root=tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/jobs/upload", files={"file": ("notes.txt", b"not video", "text/plain")}
        )

    assert response.status_code == 415


def test_start_schedules_pipeline_and_status_reaches_completed(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.db")
    completed = Event()
    app = create_app(
        repository=repository,
        data_root=tmp_path,
        pipeline_factory=lambda: ImmediatePipeline(repository, completed),
    )
    job = repository.create(str(tmp_path / "match.mp4"), "match.mp4")

    with TestClient(app) as client:
        response = client.post(f"/jobs/{job.id}/start")
        assert response.status_code == 202
        assert completed.wait(timeout=2)
        status = client.get(f"/jobs/{job.id}/status")

    assert status.json()["status"] == "completed"
    assert status.json()["progress"] == 100


def test_start_reserves_job_before_submit_and_rejects_duplicate_start(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.db")
    entered = Event()
    release = Event()
    app = create_app(
        repository=repository,
        data_root=tmp_path,
        pipeline_factory=lambda: BlockingPipeline(repository, entered, release),
    )
    job = repository.create(str(tmp_path / "match.mp4"), "match.mp4")

    with TestClient(app) as client:
        first = client.post(f"/jobs/{job.id}/start")
        assert entered.wait(timeout=2)
        second = client.post(f"/jobs/{job.id}/start")
        release.set()

    assert first.status_code == 202
    assert second.status_code == 409


def test_background_model_initialization_failure_marks_job_failed(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.db")
    attempted = Event()

    def broken_factory():
        attempted.set()
        raise RuntimeError("model could not load")

    app = create_app(
        repository=repository,
        data_root=tmp_path,
        pipeline_factory=broken_factory,
    )
    job = repository.create(str(tmp_path / "match.mp4"), "match.mp4")

    with TestClient(app) as client:
        response = client.post(f"/jobs/{job.id}/start")
        assert attempted.wait(timeout=2)
        for _ in range(100):
            failed = repository.get(job.id)
            if failed.status is JobStatus.FAILED:
                break

    assert response.status_code == 202
    assert failed.status is JobStatus.FAILED
    assert failed.error == "model could not load"


def test_start_applies_bounded_admission_backpressure(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.db")
    entered = Event()
    release = Event()
    app = create_app(
        repository=repository,
        data_root=tmp_path,
        pipeline_factory=lambda: BlockingPipeline(repository, entered, release),
        max_pending_jobs=1,
    )
    first_job = repository.create(str(tmp_path / "first.mp4"), "first.mp4")
    second_job = repository.create(str(tmp_path / "second.mp4"), "second.mp4")

    with TestClient(app) as client:
        first = client.post(f"/jobs/{first_job.id}/start")
        assert entered.wait(timeout=2)
        second = client.post(f"/jobs/{second_job.id}/start")
        release.set()

    assert first.status_code == 202
    assert second.status_code == 429
    assert repository.get(second_job.id).status is JobStatus.CREATED


def test_stop_created_job_and_list_normalized_children(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.db")
    app = create_app(repository=repository, data_root=tmp_path)
    job = repository.create(str(tmp_path / "match.mp4"), "match.mp4")

    with TestClient(app) as client:
        stopped = client.post(f"/jobs/{job.id}/stop")
        jobs = client.get("/jobs")
        events = client.get(f"/jobs/{job.id}/events")
        tracks = client.get(f"/jobs/{job.id}/tracks")
        video = client.get(f"/jobs/{job.id}/annotated-video")

    assert stopped.json()["status"] == "stopped"
    assert len(jobs.json()) == 1
    assert events.json() == []
    assert tracks.json() == []
    assert video.status_code == 409


def test_unknown_job_returns_not_found(tmp_path: Path):
    app = create_app(repository=JobRepository(tmp_path / "jobs.db"), data_root=tmp_path)

    with TestClient(app) as client:
        response = client.get("/jobs/missing")

    assert response.status_code == 404
