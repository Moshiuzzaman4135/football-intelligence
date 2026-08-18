from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Event

import pytest

from football_intelligence.domain import UploadStatus
from football_intelligence.object_store import (
    InMemoryObjectStore,
    MultipartCompletionUncertain,
)
from football_intelligence.persistence import (
    SQLAlchemyJobRepository,
    SQLAlchemyUploadRepository,
    create_persistence_engine,
    create_schema,
)
from football_intelligence.uploads import (
    CompletedPart,
    MultipartUploadService,
    UploadConflict,
    UploadExpired,
)


def test_new_service_instance_resumes_and_completes_durable_upload(tmp_path: Path):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'state.db'}")
    create_schema(engine)
    upload_store = SQLAlchemyUploadRepository(engine)
    jobs = SQLAlchemyJobRepository(engine)
    objects = InMemoryObjectStore()
    first_service = MultipartUploadService(
        object_store=objects,
        job_store=jobs,
        upload_store=upload_store,
    )
    body = b"video-body"
    created = first_service.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=len(body),
        checksum_sha256=sha256(body).hexdigest(),
    )
    private = upload_store.get(created.id)
    part = objects.upload_part(
        private.storage_upload_id, private.object_key, 1, body
    )

    restarted = MultipartUploadService(
        object_store=objects,
        job_store=jobs,
        upload_store=SQLAlchemyUploadRepository(engine),
    )
    resumed = restarted.get_upload(created.id, "operator-1")
    job = restarted.complete_upload(
        created.id,
        "operator-1",
        [CompletedPart(part_number=1, etag=part.etag)],
    )

    assert resumed.uploaded_parts[0].etag == part.etag
    assert job.id == created.id
    assert restarted.get_upload(created.id, "operator-1").job_id == job.id


class AcknowledgementLossStore(InMemoryObjectStore):
    def complete_multipart(self, storage_upload_id, object_key, parts):
        super().complete_multipart(storage_upload_id, object_key, parts)
        raise MultipartCompletionUncertain("connection closed after commit")


def test_completion_acknowledgement_loss_recovers_by_probing_object(tmp_path: Path):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'state.db'}")
    create_schema(engine)
    uploads = SQLAlchemyUploadRepository(engine)
    jobs = SQLAlchemyJobRepository(engine)
    objects = AcknowledgementLossStore()
    service = MultipartUploadService(
        object_store=objects, job_store=jobs, upload_store=uploads
    )
    body = b"video-body"
    created = service.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=len(body),
        checksum_sha256=sha256(body).hexdigest(),
    )
    private = uploads.get(created.id)
    part = objects.upload_part(private.storage_upload_id, private.object_key, 1, body)

    job = service.complete_upload(
        created.id,
        "operator-1",
        [CompletedPart(part_number=1, etag=part.etag)],
    )

    assert job.id == created.id
    assert uploads.get(created.id).status is UploadStatus.COMPLETED


def test_job_commit_unknown_replay_returns_preallocated_job(tmp_path: Path):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'state.db'}")
    create_schema(engine)
    uploads = SQLAlchemyUploadRepository(engine)
    durable_jobs = SQLAlchemyJobRepository(engine)
    objects = InMemoryObjectStore()

    class CommitUnknownJobs:
        def __init__(self):
            self.lost_ack = False

        def create_with_id(self, job_id, source_path, original_filename):
            job = durable_jobs.create_with_id(job_id, source_path, original_filename)
            if not self.lost_ack:
                self.lost_ack = True
                raise RuntimeError("job commit acknowledgement lost")
            return job

        def get(self, job_id):
            return durable_jobs.get(job_id)

    service = MultipartUploadService(
        object_store=objects,
        job_store=CommitUnknownJobs(),
        upload_store=uploads,
    )
    body = b"video-body"
    created = service.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=len(body),
        checksum_sha256=sha256(body).hexdigest(),
    )
    private = uploads.get(created.id)
    part = objects.upload_part(private.storage_upload_id, private.object_key, 1, body)
    completion = [CompletedPart(part_number=1, etag=part.etag)]

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        service.complete_upload(created.id, "operator-1", completion)
    replay = service.complete_upload(created.id, "operator-1", completion)

    assert replay.id == created.id
    assert durable_jobs.list() == [replay]
    assert uploads.get(created.id).status is UploadStatus.COMPLETED


class RacingCompletionStore(InMemoryObjectStore):
    def __init__(self):
        super().__init__()
        self.barrier = Barrier(2)

    def complete_multipart(self, storage_upload_id, object_key, parts):
        self.barrier.wait(timeout=5)
        return super().complete_multipart(storage_upload_id, object_key, parts)


def test_two_service_instances_converge_on_one_completion_and_job(tmp_path: Path):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'state.db'}")
    create_schema(engine)
    uploads = SQLAlchemyUploadRepository(engine)
    jobs = SQLAlchemyJobRepository(engine)
    objects = RacingCompletionStore()
    creator = MultipartUploadService(
        object_store=objects, job_store=jobs, upload_store=uploads
    )
    body = b"video-body"
    created = creator.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=len(body),
        checksum_sha256=sha256(body).hexdigest(),
    )
    private = uploads.get(created.id)
    part = objects.upload_part(private.storage_upload_id, private.object_key, 1, body)
    completion = [CompletedPart(part_number=1, etag=part.etag)]
    services = (
        MultipartUploadService(
            object_store=objects,
            job_store=jobs,
            upload_store=SQLAlchemyUploadRepository(engine),
        ),
        MultipartUploadService(
            object_store=objects,
            job_store=jobs,
            upload_store=SQLAlchemyUploadRepository(engine),
        ),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda service: service.complete_upload(
                    created.id, "operator-1", completion
                ),
                services,
            )
        )

    assert results[0].id == results[1].id == created.id
    assert jobs.list() == [results[0]]
    assert uploads.get(created.id).status is UploadStatus.COMPLETED


class BlockingValidationStore(InMemoryObjectStore):
    def __init__(self):
        super().__init__()
        self.block_key = None
        self.validation_started = Event()
        self.release_validation = Event()

    def iter_object(self, object_key, chunk_size=1024 * 1024):
        if object_key == self.block_key:
            self.validation_started.set()
            assert self.release_validation.wait(timeout=5)
        yield from super().iter_object(object_key, chunk_size)


def test_slow_validation_does_not_block_unrelated_upload(tmp_path: Path):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'state.db'}")
    create_schema(engine)
    uploads = SQLAlchemyUploadRepository(engine)
    jobs = SQLAlchemyJobRepository(engine)
    objects = BlockingValidationStore()
    service = MultipartUploadService(
        object_store=objects, job_store=jobs, upload_store=uploads
    )

    def prepare(name: str, body: bytes):
        created = service.create_upload(
            owner_id="operator-1",
            filename=name,
            size_bytes=len(body),
            checksum_sha256=sha256(body).hexdigest(),
        )
        private = uploads.get(created.id)
        part = objects.upload_part(
            private.storage_upload_id, private.object_key, 1, body
        )
        return created, private, [CompletedPart(part_number=1, etag=part.etag)]

    slow, slow_private, slow_parts = prepare("slow.mp4", b"slow")
    fast, _, fast_parts = prepare("fast.mp4", b"fast")
    objects.block_key = slow_private.object_key

    with ThreadPoolExecutor(max_workers=2) as executor:
        slow_future = executor.submit(
            service.complete_upload, slow.id, "operator-1", slow_parts
        )
        assert objects.validation_started.wait(timeout=5)
        fast_future = executor.submit(
            service.complete_upload, fast.id, "operator-1", fast_parts
        )
        fast_job = fast_future.result(timeout=2)
        objects.release_validation.set()
        slow_job = slow_future.result(timeout=5)

    assert fast_job.id == fast.id
    assert slow_job.id == slow.id


def test_cleanup_expires_validated_object_and_completing_parts(tmp_path: Path):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'state.db'}")
    create_schema(engine)
    uploads = SQLAlchemyUploadRepository(engine)
    durable_jobs = SQLAlchemyJobRepository(engine)
    objects = InMemoryObjectStore()
    service = MultipartUploadService(
        object_store=objects,
        job_store=durable_jobs,
        upload_store=uploads,
    )
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    def prepare(name: str, body: bytes):
        created = service.create_upload(
            owner_id="operator-1",
            filename=name,
            size_bytes=len(body),
            checksum_sha256=sha256(body).hexdigest(),
            now=now,
            expires_in=timedelta(minutes=5),
        )
        private = uploads.get(created.id)
        part = objects.upload_part(
            private.storage_upload_id, private.object_key, 1, body
        )
        return created, private, part

    validated, validated_private, validated_part = prepare("validated.mp4", b"done")
    completing_record = uploads.compare_and_set(
        validated.id,
        expected_status=UploadStatus.ACTIVE,
        expected_version=0,
        values={
            "status": UploadStatus.COMPLETING.value,
            "completion_parts": [validated_part],
        },
    )
    assert completing_record is not None
    completed_object = objects.complete_multipart(
        validated_private.storage_upload_id,
        validated_private.object_key,
        [validated_part],
    )
    validated_record = uploads.compare_and_set(
        validated.id,
        expected_status=UploadStatus.COMPLETING,
        expected_version=completing_record.version,
        values={
            "status": UploadStatus.VALIDATED.value,
            "validated_parts": [validated_part],
            "object_size_bytes": 4,
            "object_checksum_sha256": sha256(b"done").hexdigest(),
            "object_etag": completed_object.etag,
        },
    )
    assert validated_record is not None
    completing, completing_private, completing_part = prepare(
        "completing.mp4", b"parts"
    )
    uploads.compare_and_set(
        completing.id,
        expected_status=UploadStatus.ACTIVE,
        expected_version=0,
        values={
            "status": UploadStatus.COMPLETING.value,
            "completion_parts": [completing_part],
        },
    )

    cleaned = service.cleanup_expired(now=now + timedelta(minutes=5))

    assert cleaned == 2
    assert uploads.get(validated.id).status is UploadStatus.EXPIRED
    assert uploads.get(completing.id).status is UploadStatus.EXPIRED
    assert not objects.object_exists(validated_private.object_key)
    assert objects.list_parts(
        completing_private.storage_upload_id, completing_private.object_key
    ) == []


def test_cleanup_retries_transient_storage_failure(tmp_path: Path):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'state.db'}")
    create_schema(engine)
    uploads = SQLAlchemyUploadRepository(engine)
    jobs = SQLAlchemyJobRepository(engine)

    class FailDeleteOnceStore(InMemoryObjectStore):
        def __init__(self):
            super().__init__()
            self.failed = False

        def delete_object(self, object_key):
            if not self.failed:
                self.failed = True
                raise TimeoutError("object store unavailable")
            return super().delete_object(object_key)

    objects = FailDeleteOnceStore()
    service = MultipartUploadService(
        object_store=objects,
        job_store=jobs,
        upload_store=uploads,
    )
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    created = service.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=4,
        checksum_sha256=sha256(b"data").hexdigest(),
        now=now,
        expires_in=timedelta(minutes=1),
    )

    with pytest.raises(UploadExpired):
        service.get_upload(
            created.id, "operator-1", now=now + timedelta(minutes=1)
        )
    assert uploads.get(created.id).status is UploadStatus.EXPIRED
    assert service.cleanup_expired(now=now + timedelta(minutes=2)) == 1
    assert service.cleanup_expired(now=now + timedelta(minutes=3)) == 0


def test_finalizing_upload_cannot_expire_between_job_insert_and_state_commit(
    tmp_path: Path,
):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'state.db'}")
    create_schema(engine)
    uploads = SQLAlchemyUploadRepository(engine)
    durable_jobs = SQLAlchemyJobRepository(engine)
    objects = InMemoryObjectStore()
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    class UnavailableJobs:
        def create_with_id(self, job_id, source_path, original_filename):
            raise RuntimeError("database unavailable")

        def get(self, job_id):
            return durable_jobs.get(job_id)

    validator = MultipartUploadService(
        object_store=objects,
        job_store=UnavailableJobs(),
        upload_store=uploads,
    )
    body = b"video-body"
    created = validator.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=len(body),
        checksum_sha256=sha256(body).hexdigest(),
        now=now,
        expires_in=timedelta(minutes=1),
    )
    private = uploads.get(created.id)
    part = objects.upload_part(private.storage_upload_id, private.object_key, 1, body)
    completion = [CompletedPart(part_number=1, etag=part.etag)]
    with pytest.raises(RuntimeError, match="database unavailable"):
        validator.complete_upload(created.id, "operator-1", completion, now=now)

    job_inserted = Event()
    release_job_ack = Event()

    class BlockingJobs:
        def create_with_id(self, job_id, source_path, original_filename):
            job = durable_jobs.create_with_id(job_id, source_path, original_filename)
            job_inserted.set()
            assert release_job_ack.wait(timeout=5)
            return job

        def get(self, job_id):
            return durable_jobs.get(job_id)

    finalizer = MultipartUploadService(
        object_store=objects,
        job_store=BlockingJobs(),
        upload_store=SQLAlchemyUploadRepository(engine),
    )
    cleaner = MultipartUploadService(
        object_store=objects,
        job_store=durable_jobs,
        upload_store=SQLAlchemyUploadRepository(engine),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        completion_future = executor.submit(
            finalizer.complete_upload,
            created.id,
            "operator-1",
            completion,
            now=now + timedelta(seconds=30),
        )
        assert job_inserted.wait(timeout=5)
        assert uploads.get(created.id).status is UploadStatus.FINALIZING
        assert cleaner.cleanup_expired(now=now + timedelta(minutes=2)) == 0
        assert objects.object_exists(private.object_key)
        release_job_ack.set()
        job = completion_future.result(timeout=5)

    assert job.id == created.id
    assert uploads.get(created.id).status is UploadStatus.COMPLETED
    assert objects.object_exists(private.object_key)


def test_aborted_cleanup_retries_before_session_expiry(tmp_path: Path):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'state.db'}")
    create_schema(engine)
    uploads = SQLAlchemyUploadRepository(engine)
    jobs = SQLAlchemyJobRepository(engine)

    class FailAbortOnceStore(InMemoryObjectStore):
        def __init__(self):
            super().__init__()
            self.failed = False

        def abort_multipart(self, storage_upload_id, object_key):
            if not self.failed:
                self.failed = True
                raise TimeoutError("object store unavailable")
            return super().abort_multipart(storage_upload_id, object_key)

    objects = FailAbortOnceStore()
    service = MultipartUploadService(
        object_store=objects,
        job_store=jobs,
        upload_store=uploads,
    )
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    created = service.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=4,
        checksum_sha256=sha256(b"data").hexdigest(),
        now=now,
    )
    private = uploads.get(created.id)
    objects.upload_part(private.storage_upload_id, private.object_key, 1, b"data")

    service.abort_upload(created.id, "operator-1", now=now)

    pending = uploads.get(created.id)
    assert pending.status is UploadStatus.ABORTED
    assert pending.cleanup_completed_at is None
    assert service.cleanup_expired(now=now + timedelta(seconds=1)) == 1
    assert uploads.get(created.id).cleanup_completed_at is not None
    assert objects.list_parts(private.storage_upload_id, private.object_key) == []


def test_failed_validation_cleanup_retries_before_session_expiry(tmp_path: Path):
    engine = create_persistence_engine(f"sqlite:///{tmp_path / 'state.db'}")
    create_schema(engine)
    uploads = SQLAlchemyUploadRepository(engine)
    jobs = SQLAlchemyJobRepository(engine)

    class FailDeleteOnceStore(InMemoryObjectStore):
        def __init__(self):
            super().__init__()
            self.failed = False

        def delete_object(self, object_key):
            if not self.failed:
                self.failed = True
                raise TimeoutError("object store unavailable")
            return super().delete_object(object_key)

    objects = FailDeleteOnceStore()
    service = MultipartUploadService(
        object_store=objects,
        job_store=jobs,
        upload_store=uploads,
    )
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    body = b"video-body"
    created = service.create_upload(
        owner_id="operator-1",
        filename="match.mp4",
        size_bytes=len(body),
        checksum_sha256="0" * 64,
        now=now,
    )
    private = uploads.get(created.id)
    part = objects.upload_part(private.storage_upload_id, private.object_key, 1, body)

    with pytest.raises(UploadConflict, match="checksum"):
        service.complete_upload(
            created.id,
            "operator-1",
            [CompletedPart(part_number=1, etag=part.etag)],
            now=now,
        )

    pending = uploads.get(created.id)
    assert pending.status is UploadStatus.FAILED
    assert pending.cleanup_completed_at is None
    assert service.cleanup_expired(now=now + timedelta(seconds=1)) == 1
    assert uploads.get(created.id).cleanup_completed_at is not None
    assert not objects.object_exists(private.object_key)
