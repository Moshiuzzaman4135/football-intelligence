from hashlib import sha256
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient

from football_intelligence.api import create_app
from football_intelligence.object_store import InMemoryObjectStore
from football_intelligence.settings import Settings
from football_intelligence.storage import JobRepository
from football_intelligence.uploads import MultipartUploadService


def test_multipart_api_create_presign_resume_complete_and_abort(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.db")
    objects = InMemoryObjectStore()
    uploads = MultipartUploadService(object_store=objects, job_store=repository)
    app = create_app(repository=repository, data_root=tmp_path, upload_service=uploads)
    owner = {"X-Owner-ID": "operator-1"}
    body = b"video-body"

    with TestClient(app) as client:
        created = client.post(
            "/uploads",
            headers=owner,
            json={
                "filename": "match.mp4",
                "size_bytes": len(body),
                "checksum_sha256": sha256(body).hexdigest(),
            },
        )
        upload = created.json()
        presigned = client.post(
            f"/uploads/{upload['id']}/parts/1/presign",
            headers=owner,
            json={"checksum_sha256": sha256(body).hexdigest()},
        )
        session = uploads.upload_store.get(upload["id"])
        stored = objects.upload_part(
            session.storage_upload_id, session.object_key, 1, body
        )
        resumed = client.get(f"/uploads/{upload['id']}", headers=owner)
        completed = client.post(
            f"/uploads/{upload['id']}/complete",
            headers=owner,
            json={"parts": [{"part_number": 1, "etag": stored.etag}]},
        )

        another = client.post(
            "/uploads",
            headers=owner,
            json={
                "filename": "another.mov",
                "size_bytes": 4,
                "checksum_sha256": sha256(b"data").hexdigest(),
            },
        ).json()
        aborted = client.delete(f"/uploads/{another['id']}", headers=owner)

    assert created.status_code == 201
    assert upload["part_size_bytes"] == 16 * 1024 * 1024
    assert presigned.status_code == 200
    assert presigned.json()["expected_size_bytes"] == len(body)
    assert presigned.json()["url"].startswith("memory://")
    assert presigned.json()["required_headers"]["Content-Length"] == str(len(body))
    assert resumed.json()["uploaded_parts"] == [
        {
            "part_number": 1,
            "size_bytes": len(body),
            "etag": stored.etag,
            "checksum_sha256": sha256(body).hexdigest(),
        }
    ]
    assert completed.status_code == 201
    assert completed.json()["original_filename"] == "match.mp4"
    assert aborted.status_code == 204
    assert repository.list()[0].id == completed.json()["id"]


def test_multipart_api_enforces_owner_and_maps_validation_errors(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.db")
    objects = InMemoryObjectStore()
    uploads = MultipartUploadService(object_store=objects, job_store=repository)
    app = create_app(repository=repository, data_root=tmp_path, upload_service=uploads)

    with TestClient(app) as client:
        missing_owner = client.post(
            "/uploads",
            json={"filename": "match.mp4", "size_bytes": 1, "checksum_sha256": "a" * 64},
        )
        unsupported = client.post(
            "/uploads",
            headers={"X-Owner-ID": "operator-1"},
            json={"filename": "match.avi", "size_bytes": 1, "checksum_sha256": "a" * 64},
        )
        upload = client.post(
            "/uploads",
            headers={"X-Owner-ID": "operator-1"},
            json={"filename": "match.mkv", "size_bytes": 1, "checksum_sha256": "a" * 64},
        ).json()
        forbidden = client.get(
            f"/uploads/{upload['id']}", headers={"X-Owner-ID": "operator-2"}
        )
        missing = client.get(
            "/uploads/missing", headers={"X-Owner-ID": "operator-1"}
        )

    assert missing_owner.status_code == 422
    assert unsupported.status_code == 422
    assert forbidden.status_code == 403
    assert missing.status_code == 404


def test_app_lifespan_schedules_expired_upload_cleanup(tmp_path: Path):
    repository = JobRepository(tmp_path / "jobs.db")
    uploads = MultipartUploadService(
        object_store=InMemoryObjectStore(), job_store=repository
    )
    cleaned = Event()

    def cleanup_expired(**_kwargs):
        cleaned.set()
        return 0

    uploads.cleanup_expired = cleanup_expired
    app = create_app(
        repository=repository,
        data_root=tmp_path,
        upload_service=uploads,
        settings=Settings(_env_file=None, upload_cleanup_interval_seconds=0.01),
    )

    with TestClient(app):
        assert cleaned.wait(timeout=1)
