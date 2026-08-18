#!/usr/bin/env python3
"""Live end-to-end check of the browser full-match flow against a running stack.

This script performs the exact API calls the browser uploader page makes
(``GET /full-match``, multipart create/presign/PUT/complete, full-match run,
polling, and result reads) and verifies each response. Run it from the host
against a started Compose stack:

    python3 tools/live_fullmatch_check.py --video /path/to/source.mp4

The presigned part URLs must be host-reachable (the default
``FOOTBALL_S3_PUBLIC_ENDPOINT_URL=http://127.0.0.1:9010`` in ``.env``).
"""

from __future__ import annotations

import argparse
import hashlib
import math
import time
from pathlib import Path

import requests

PART_SIZE = 16 * 1024 * 1024
HEADERS = {"X-Owner-ID": "live-check"}


def fail(message: str) -> None:
    raise SystemExit(f"FAILED: {message}")


def sha256_hex(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8010")
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--owner-id", default="live-check")
    args = parser.parse_args()

    video = args.video
    if not video.is_file():
        fail(f"video not found: {video}")
    api = args.api.rstrip("/")
    headers = {"X-Owner-ID": args.owner_id}

    page = requests.get(f"{api}/full-match", timeout=10)
    if page.status_code != 200 or "fm-endpoints" not in page.text:
        fail("GET /full-match did not serve the browser page")
    print("page served: GET /full-match -> 200")

    size_bytes = video.stat().st_size
    checksum = sha256_hex(video)
    print(f"source {video.name}: {size_bytes} bytes sha256 {checksum[:16]}...")

    created = requests.post(
        f"{api}/uploads",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "filename": video.name,
            "size_bytes": size_bytes,
            "checksum_sha256": checksum,
        },
        timeout=30,
    )
    if created.status_code != 201:
        fail(f"create upload: {created.status_code} {created.text}")
    session = created.json()
    upload_id = session["id"]
    part_size = session["part_size_bytes"]
    print(f"upload session {upload_id} created")

    part_count = math.ceil(size_bytes / part_size)
    parts = []
    with video.open("rb") as source:
        for number in range(1, part_count + 1):
            expected_size = (
                part_size if number < part_count else size_bytes - part_size * (part_count - 1)
            )
            part_bytes = source.read(expected_size)
            part_hash = hashlib.sha256(part_bytes).hexdigest()
            presign = requests.post(
                f"{api}/uploads/{upload_id}/parts/{number}/presign",
                headers={**headers, "Content-Type": "application/json"},
                json={"checksum_sha256": part_hash},
                timeout=30,
            )
            if presign.status_code != 200:
                fail(f"presign part {number}: {presign.status_code} {presign.text}")
            presigned = presign.json()
            put_headers = {
                key: value
                for key, value in presigned["required_headers"].items()
                if key.lower() != "content-length"
            }
            put = requests.put(
                presigned["url"],
                headers=put_headers,
                data=part_bytes,
                timeout=120,
            )
            if put.status_code != 200:
                fail(f"PUT part {number}: {put.status_code} {put.text[:200]}")
            etag = put.headers.get("etag", "")
            parts.append({"part_number": number, "etag": etag})
            print(
                f"part {number}/{part_count} ({expected_size} bytes) "
                f"uploaded etag {etag[:16]}..."
            )

    completed = requests.post(
        f"{api}/uploads/{upload_id}/complete",
        headers={**headers, "Content-Type": "application/json"},
        json={"parts": parts},
        timeout=120,
    )
    if completed.status_code != 201:
        fail(f"complete upload: {completed.status_code} {completed.text}")
    job = completed.json()
    job_id = job["id"]
    print(f"job {job_id} created (status {job['status']})")

    started = requests.post(
        f"{api}/jobs/{job_id}/full-match/run",
        headers=headers,
        timeout=30,
    )
    if started.status_code not in (200, 202):
        fail(f"full-match run: {started.status_code} {started.text}")
    print(f"full-match run accepted: {started.json()}")

    deadline = time.time() + 900
    last_status = ""
    while time.time() < deadline:
        status = requests.get(f"{api}/jobs/{job_id}/status", headers=headers, timeout=30)
        if status.status_code != 200:
            fail(f"job status: {status.status_code} {status.text}")
        job_status = status.json()
        fm = requests.get(
            f"{api}/jobs/{job_id}/full-match/status", headers=headers, timeout=30
        )
        manifest = None
        if fm.status_code == 200:
            manifest = fm.json()["manifest"]
        done_chunks = 0
        if manifest and manifest.get("chunks"):
            done_chunks = sum(
                1 for chunk in manifest["chunks"] if chunk.get("status") == "completed"
            )
        progress = job_status.get("progress", 0)
        line = (
            f"job={job_status['status']} progress={progress}% chunks={done_chunks}/"
            f"{len(manifest['chunks']) if manifest and manifest.get('chunks') else '?'}"
        )
        if line != last_status:
            print(line)
            last_status = line
        if job_status["status"] == "completed":
            break
        if job_status["status"] in {"failed", "stopped"}:
            fail(f"job ended {job_status['status']}: {job_status}")
        time.sleep(3)
    else:
        fail("job did not complete within the timeout")

    events = requests.get(f"{api}/jobs/{job_id}/events", headers=headers, timeout=30)
    if events.status_code != 200:
        fail(f"events: {events.status_code}")
    print(f"events: {len(events.json())} candidates")

    scoreboard = requests.get(
        f"{api}/jobs/{job_id}/scoreboard", headers=headers, timeout=30
    )
    if scoreboard.status_code not in (200, 409):
        fail(f"scoreboard: {scoreboard.status_code}")
    count = len(scoreboard.json()) if scoreboard.status_code == 200 else 0
    print(f"scoreboard observations: {count}")

    heatmap = requests.get(f"{api}/jobs/{job_id}/heat-map", headers=headers, timeout=30)
    if heatmap.status_code != 200 or not heatmap.content.startswith(b"\x89PNG"):
        fail(f"heat map: {heatmap.status_code}")
    print(f"heat map: PNG {len(heatmap.content)} bytes")

    video_response = requests.get(
        f"{api}/jobs/{job_id}/annotated-video", headers=headers, timeout=60
    )
    if video_response.status_code != 200:
        fail(f"annotated video: {video_response.status_code}")
    print(f"annotated video: {len(video_response.content)} bytes served")

    print("LIVE FULL-MATCH FLOW OK")


if __name__ == "__main__":
    main()
