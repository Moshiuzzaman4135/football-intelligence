import os
from hashlib import sha256
from pathlib import Path

import pytest
import requests

from football_intelligence.object_store import S3ObjectStore
from football_intelligence.storage import JobRepository
from football_intelligence.uploads import PART_SIZE_BYTES, CompletedPart, MultipartUploadService


@pytest.mark.integration
def test_real_minio_multipart_upload_is_resumable_and_validated(tmp_path: Path):
    if os.environ.get("FOOTBALL_S3_INTEGRATION") != "1":
        pytest.skip("set FOOTBALL_S3_INTEGRATION=1 to exercise a live MinIO service")

    store = S3ObjectStore(
        bucket=os.environ["FOOTBALL_S3_BUCKET"],
        endpoint_url=os.environ["FOOTBALL_S3_ENDPOINT_URL"],
        access_key=os.environ["FOOTBALL_S3_ACCESS_KEY"],
        secret_key=os.environ["FOOTBALL_S3_SECRET_KEY"],
    )
    store.ensure_bucket()
    service = MultipartUploadService(
        object_store=store, job_store=JobRepository(tmp_path / "jobs.db")
    )
    first_body = b"a" * PART_SIZE_BYTES
    second_body = b"tail"
    body_checksum = sha256(first_body + second_body).hexdigest()
    upload = service.create_upload(
        owner_id="integration-operator",
        filename="match.mp4",
        size_bytes=len(first_body) + len(second_body),
        checksum_sha256=body_checksum,
    )

    try:
        first_presign = service.presign_part(
            upload.id,
            "integration-operator",
            1,
            checksum_sha256=sha256(first_body).hexdigest(),
        )
        second_presign = service.presign_part(
            upload.id,
            "integration-operator",
            2,
            checksum_sha256=sha256(second_body).hexdigest(),
        )
        oversize_response = requests.put(
            first_presign.url,
            data=first_body + b"x",
            headers=first_presign.required_headers,
            timeout=30,
        )
        assert oversize_response.status_code in {400, 403}
        first_response = requests.put(
            first_presign.url,
            data=first_body,
            headers=first_presign.required_headers,
            timeout=30,
        )
        second_response = requests.put(
            second_presign.url,
            data=second_body,
            headers=second_presign.required_headers,
            timeout=30,
        )
        first_response.raise_for_status()
        second_response.raise_for_status()

        resumed = service.get_upload(upload.id, "integration-operator")
        assert [part.part_number for part in resumed.uploaded_parts] == [1, 2]
        job = service.complete_upload(
            upload.id,
            "integration-operator",
            [
                CompletedPart(part_number=part.part_number, etag=part.etag)
                for part in resumed.uploaded_parts
            ],
        )

        assert job.source_path == f"s3://{store.bucket}/{upload.object_key}"
        assert store.object_exists(upload.object_key)
    finally:
        record = service.upload_store.get(upload.id)
        store.abort_multipart(record.storage_upload_id, record.object_key)
        if store.object_exists(upload.object_key):
            store.delete_object(upload.object_key)
