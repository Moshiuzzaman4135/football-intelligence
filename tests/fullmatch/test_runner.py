from pathlib import Path

import pytest

from football_intelligence.domain import (
    EventEvidence,
    FootballEvent,
    JobStatus,
    ScoreboardObservation,
    ScoreboardRegion,
)
from football_intelligence.fullmatch.heatmap import ScreenSpaceHeatMap
from football_intelligence.fullmatch.manifest import ChunkRecord
from football_intelligence.fullmatch.media import MediaProbe
from football_intelligence.fullmatch.runner import (
    ChunkResult,
    FullMatchRunner,
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

    def _prepare_media(self, job, workspace):
        proxy = workspace / "proxy.mp4"
        proxy.write_bytes(b"bounded proxy")
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
        return metadata.model_copy(update={"path": str(workspace / "source.mp4")}), metadata

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


def test_track_namespace_is_deterministic_and_chunk_local():
    assert namespace_track_id(0, 7) == 7
    assert namespace_track_id(1, 7) == 1_000_007
    assert namespace_track_id(1, 7) != namespace_track_id(2, 7)


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
