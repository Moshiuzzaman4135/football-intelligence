"""FastAPI lifecycle and artifact API."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, Header, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from football_intelligence.bus import EventBus
from football_intelligence.detection.factory import build_detector
from football_intelligence.domain import JobRecord, JobStatus, UploadSession
from football_intelligence.object_store import (
    FilesystemObjectStore,
    MultipartPresignUnsupported,
    S3ObjectStore,
)
from football_intelligence.persistence import (
    JobStore,
    SQLAlchemyJobRepository,
    SQLAlchemyUploadRepository,
    UploadStore,
    create_persistence_engine,
)
from football_intelligence.pipeline import Pipeline
from football_intelligence.settings import Settings
from football_intelligence.storage import InvalidJobTransition, JobNotFound, JobRepository
from football_intelligence.tracking.iou import IoUTracker
from football_intelligence.uploads import (
    CompletedPart,
    MultipartUploadService,
    PresignedPart,
    UploadConflict,
    UploadExpired,
    UploadForbidden,
    UploadNotFound,
)

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


class CreateMultipartUploadRequest(BaseModel):
    filename: str = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CompleteMultipartUploadRequest(BaseModel):
    parts: list[CompletedPart] = Field(min_length=1)


class PresignMultipartPartRequest(BaseModel):
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def create_app(
    *,
    repository: JobStore,
    data_root: str | Path,
    pipeline_factory: Callable[[], Any] | None = None,
    settings: Settings | None = None,
    max_pending_jobs: int = 4,
    upload_service: MultipartUploadService | None = None,
    upload_store: UploadStore | None = None,
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
    if upload_service is None:
        if runtime_settings.object_store_backend == "s3":
            if upload_store is None:
                raise ValueError("S3 multipart runtime requires a durable upload store")
            object_store = S3ObjectStore(
                bucket=runtime_settings.s3_bucket,
                endpoint_url=runtime_settings.s3_endpoint_url,
                presign_endpoint_url=(
                    runtime_settings.s3_public_endpoint_url or None
                ),
                access_key=runtime_settings.s3_access_key,
                secret_key=runtime_settings.s3_secret_key.get_secret_value(),
                region=runtime_settings.s3_region,
            )
            object_store.ensure_bucket()
        else:
            object_store = FilesystemObjectStore(root / "object-store")
        upload_service = MultipartUploadService(
            object_store=object_store,
            job_store=repository,
            upload_store=upload_store,
        )

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
        async def cleanup_uploads() -> None:
            while True:
                await asyncio.sleep(runtime_settings.upload_cleanup_interval_seconds)
                await asyncio.to_thread(upload_service.cleanup_expired)

        cleanup_task = asyncio.create_task(cleanup_uploads())
        try:
            yield
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
            executor.shutdown(wait=False, cancel_futures=False)

    application = FastAPI(
        title="Football Video Intelligence",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.repository = repository
    application.state.bus = bus
    application.state.executor = executor
    application.state.upload_service = upload_service

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

    def multipart_error(error: Exception) -> HTTPException:
        if isinstance(error, UploadForbidden):
            return HTTPException(status_code=403, detail=str(error))
        if isinstance(error, UploadNotFound):
            return HTTPException(status_code=404, detail="upload not found")
        if isinstance(error, UploadExpired):
            return HTTPException(status_code=410, detail=str(error))
        if isinstance(error, UploadConflict):
            return HTTPException(status_code=409, detail=str(error))
        if isinstance(error, MultipartPresignUnsupported):
            return HTTPException(status_code=503, detail=str(error))
        return HTTPException(status_code=422, detail=str(error))

    @application.post(
        "/uploads", response_model=UploadSession, status_code=201
    )
    def create_multipart_upload(
        request: CreateMultipartUploadRequest,
        owner_id: Annotated[str, Header(alias="X-Owner-ID", min_length=1)],
    ) -> UploadSession:
        try:
            upload = upload_service.create_upload(
                owner_id=owner_id,
                filename=request.filename,
                size_bytes=request.size_bytes,
                checksum_sha256=request.checksum_sha256,
            )
        except ValueError as error:
            raise multipart_error(error) from error
        return upload

    @application.post(
        "/uploads/{upload_id}/parts/{part_number}/presign",
        response_model=PresignedPart,
    )
    def presign_multipart_part(
        upload_id: str,
        part_number: int,
        request: PresignMultipartPartRequest,
        owner_id: Annotated[str, Header(alias="X-Owner-ID", min_length=1)],
    ) -> PresignedPart:
        try:
            return upload_service.presign_part(
                upload_id,
                owner_id,
                part_number,
                checksum_sha256=request.checksum_sha256,
            )
        except (
            ValueError,
            UploadNotFound,
            UploadForbidden,
            UploadExpired,
            UploadConflict,
            MultipartPresignUnsupported,
        ) as error:
            raise multipart_error(error) from error

    @application.get("/uploads/{upload_id}", response_model=UploadSession)
    def read_multipart_upload(
        upload_id: str,
        owner_id: Annotated[str, Header(alias="X-Owner-ID", min_length=1)],
    ) -> UploadSession:
        try:
            return upload_service.get_upload(upload_id, owner_id)
        except (UploadNotFound, UploadForbidden, UploadExpired, UploadConflict) as error:
            raise multipart_error(error) from error

    @application.post(
        "/uploads/{upload_id}/complete", response_model=JobRecord, status_code=201
    )
    def complete_multipart_upload(
        upload_id: str,
        request: CompleteMultipartUploadRequest,
        owner_id: Annotated[str, Header(alias="X-Owner-ID", min_length=1)],
    ) -> JobRecord:
        try:
            return upload_service.complete_upload(upload_id, owner_id, request.parts)
        except (UploadNotFound, UploadForbidden, UploadExpired, UploadConflict) as error:
            raise multipart_error(error) from error

    @application.delete("/uploads/{upload_id}", status_code=204)
    def abort_multipart_upload(
        upload_id: str,
        owner_id: Annotated[str, Header(alias="X-Owner-ID", min_length=1)],
    ) -> Response:
        try:
            upload_service.abort_upload(upload_id, owner_id)
        except (UploadNotFound, UploadForbidden, UploadExpired, UploadConflict) as error:
            raise multipart_error(error) from error
        return Response(status_code=204)

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
if DEFAULT_SETTINGS.database_url:
    DEFAULT_ENGINE = create_persistence_engine(DEFAULT_SETTINGS.database_url)
    DEFAULT_REPOSITORY = SQLAlchemyJobRepository(DEFAULT_ENGINE)
    DEFAULT_UPLOAD_STORE = SQLAlchemyUploadRepository(DEFAULT_ENGINE)
else:
    DEFAULT_REPOSITORY = JobRepository(DEFAULT_DATABASE)
    DEFAULT_UPLOAD_STORE = None
app = create_app(
    repository=DEFAULT_REPOSITORY,
    data_root=DEFAULT_DATA_ROOT,
    settings=DEFAULT_SETTINGS,
    upload_store=DEFAULT_UPLOAD_STORE,
)
