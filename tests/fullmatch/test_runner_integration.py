import hashlib
import subprocess
from pathlib import Path

import pytest

from football_intelligence.domain import JobStatus
from football_intelligence.fullmatch.media import probe_media
from football_intelligence.fullmatch.runner import FullMatchRunner
from football_intelligence.object_store import InMemoryObjectStore
from football_intelligence.storage import JobRepository


@pytest.mark.integration
def test_generated_121_second_fixture_runs_two_chunks_with_audio_and_no_raw_sql(
    tmp_path: Path,
):
    source = tmp_path / "generated-match.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=160x90:r=1:d=121",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=8000:duration=121",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    body = source.read_bytes()
    object_store = InMemoryObjectStore()
    key = "uploads/generated-match.mp4"
    storage_upload_id = object_store.create_multipart(key, "video/mp4")
    part = object_store.upload_part(storage_upload_id, key, 1, body)
    object_store.complete_multipart(storage_upload_id, key, [part])
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    job = repository.create_with_id(
        "full-match-fixture",
        f"s3://football-media/{key}",
        source.name,
    )
    runner = FullMatchRunner(
        repository=repository,
        object_store=object_store,
        bucket="football-media",
        data_root=tmp_path,
    )

    completed = runner.run(job.id)

    assert completed.status is JobStatus.COMPLETED
    manifest = runner.status(job.id)
    assert len(manifest.chunks) == 2
    assert all(chunk.status == "completed" for chunk in manifest.chunks)
    chunk_media = [probe_media(chunk.output_path) for chunk in manifest.chunks]
    assert [item.duration_ms for item in chunk_media] == [120_000, 1_000]
    assert all(item.video_codec == "h264" and not item.has_audio for item in chunk_media)
    assert repository.get_tracks(job.id) == []
    assert manifest.final_artifact is not None
    output = Path(manifest.final_artifact.path)
    assert hashlib.sha256(output.read_bytes()).hexdigest() == manifest.final_artifact.sha256
    actual = probe_media(output)
    assert actual.video_codec == "h264"
    assert (actual.width, actual.height, actual.fps) == (160, 90, 1)
    assert abs(actual.duration_ms - 121_000) <= 1_000
    assert actual.has_audio is True
