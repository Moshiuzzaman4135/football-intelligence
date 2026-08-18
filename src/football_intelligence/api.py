"""FastAPI lifecycle and artifact API."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse

from football_intelligence.bus import EventBus
from football_intelligence.detection.factory import build_detector
from football_intelligence.domain import JobRecord, JobStatus
from football_intelligence.persistence import JobStore
from football_intelligence.pipeline import Pipeline
from football_intelligence.settings import Settings
from football_intelligence.storage import InvalidJobTransition, JobNotFound, JobRepository
from football_intelligence.tracking.iou import IoUTracker

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


def create_app(
    *,
    repository: JobStore,
    data_root: str | Path,
    pipeline_factory: Callable[[], Any] | None = None,
    settings: Settings | None = None,
    max_pending_jobs: int = 4,
) -> FastAPI:
    root = Path(data_root)
    upload_dir = root / "uploads"
    output_dir = root / "outputs"
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="football-job")
    admission = BoundedSemaphore(max_pending_jobs)
    bus = EventBus()
    runtime_settings = settings or Settings()

    if pipeline_factory is None:

        def pipeline_factory() -> Pipeline:
            return Pipeline(
                repository=repository,
                detector=build_detector(
                    runtime_settings.detector,
                    model_name=runtime_settings.model_name,
                    device=runtime_settings.device,
                ),
                tracker=IoUTracker(),
                output_dir=output_dir,
                bus=bus,
                max_frame_errors=runtime_settings.max_frame_errors,
            )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        executor.shutdown(wait=False, cancel_futures=False)

    application = FastAPI(
        title="Football Video Intelligence",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.repository = repository
    application.state.bus = bus
    application.state.executor = executor

    def get_job(job_id: str) -> JobRecord:
        try:
            return repository.get(job_id)
        except JobNotFound as error:
            raise HTTPException(status_code=404, detail="job not found") from error

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/jobs/upload", response_model=JobRecord, status_code=201)
    async def upload_job(file: Annotated[UploadFile, File()]) -> JobRecord:
        safe_name = Path(file.filename or "upload.mp4").name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_VIDEO_EXTENSIONS:
            raise HTTPException(status_code=415, detail="unsupported video extension")
        destination = upload_dir / f"{uuid4()}{suffix}"
        written = 0
        try:
            with destination.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="video exceeds 2 GiB limit")
                    output.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        job = repository.create(str(destination), safe_name)
        bus.publish("job.created", {"job_id": job.id, "source_path": str(destination)})
        return job

    @application.post("/jobs/{job_id}/start", status_code=202)
    def start_job(job_id: str, response: Response) -> dict[str, str]:
        job = get_job(job_id)
        if job.status is not JobStatus.CREATED:
            raise HTTPException(status_code=409, detail=f"cannot start {job.status.value} job")
        if not admission.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="job queue is full; retry later")
        try:
            repository.transition(job_id, JobStatus.RUNNING)
        except InvalidJobTransition as error:
            admission.release()
            raise HTTPException(status_code=409, detail="job was already started") from error

        def run_reserved_job() -> None:
            try:
                pipeline_factory().run(job_id)
            except Exception as error:
                current = repository.get(job_id)
                if current.status is JobStatus.STOPPING:
                    repository.transition(job_id, JobStatus.STOPPED)
                    bus.publish("job.stopped", {"job_id": job_id})
                elif current.status is JobStatus.RUNNING:
                    repository.transition(job_id, JobStatus.FAILED, error=str(error))
            finally:
                admission.release()

        try:
            executor.submit(run_reserved_job)
        except Exception as error:
            admission.release()
            repository.transition(job_id, JobStatus.FAILED, error=str(error))
            raise HTTPException(status_code=503, detail="job executor is unavailable") from error
        response.status_code = status.HTTP_202_ACCEPTED
        return {"job_id": job_id, "status": "accepted"}

    @application.post("/jobs/{job_id}/stop", response_model=JobRecord)
    def stop_job(job_id: str) -> JobRecord:
        job = get_job(job_id)
        if job.status is JobStatus.CREATED:
            stopped = repository.transition(job_id, JobStatus.STOPPED)
            bus.publish("job.stopped", {"job_id": job_id})
            return stopped
        if job.status is JobStatus.RUNNING:
            return repository.transition(job_id, JobStatus.STOPPING)
        if job.status is JobStatus.STOPPING:
            return job
        raise HTTPException(status_code=409, detail=f"cannot stop {job.status.value} job")

    @application.get("/jobs", response_model=list[JobRecord])
    def list_jobs() -> list[JobRecord]:
        return repository.list()

    @application.get("/jobs/{job_id}", response_model=JobRecord)
    def read_job(job_id: str) -> JobRecord:
        return get_job(job_id)

    @application.get("/jobs/{job_id}/status")
    def read_status(job_id: str) -> dict[str, Any]:
        job = get_job(job_id)
        return {
            "job_id": job.id,
            "status": job.status,
            "progress": job.progress,
            "error": job.error,
            "metrics": job.metrics,
        }

    @application.get("/jobs/{job_id}/events")
    def read_events(job_id: str) -> list[dict[str, Any]]:
        get_job(job_id)
        return [event.model_dump(mode="json") for event in repository.get_events(job_id)]

    @application.get("/jobs/{job_id}/tracks")
    def read_tracks(job_id: str) -> list[dict[str, Any]]:
        get_job(job_id)
        return [
            track.model_dump(mode="json") for track in repository.get_track_summaries(job_id)
        ]

    @application.get("/jobs/{job_id}/annotated-video", response_class=FileResponse)
    def annotated_video(job_id: str) -> FileResponse:
        job = get_job(job_id)
        if not job.output_path or not Path(job.output_path).is_file():
            raise HTTPException(status_code=409, detail="annotated video is not ready")
        return FileResponse(
            job.output_path,
            media_type="video/mp4",
            filename=f"{job_id}.annotated.mp4",
        )

    return application


DEFAULT_SETTINGS = Settings()
DEFAULT_DATA_ROOT = DEFAULT_SETTINGS.data_root.resolve()
DEFAULT_DATABASE = DEFAULT_DATA_ROOT / "db" / "football_intelligence.db"
app = create_app(
    repository=JobRepository(DEFAULT_DATABASE),
    data_root=DEFAULT_DATA_ROOT,
    settings=DEFAULT_SETTINGS,
)
