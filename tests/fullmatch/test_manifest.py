from pathlib import Path

from football_intelligence.fullmatch.manifest import (
    FullMatchManifest,
    ManifestStore,
    RunnerOptions,
)
from football_intelligence.fullmatch.media import MediaProbe


def _probe(path: Path, duration_ms: int = 250_000) -> MediaProbe:
    return MediaProbe(
        path=str(path),
        container="mov,mp4,m4a,3gp,3g2,mj2",
        video_codec="h264",
        width=320,
        height=180,
        fps=25,
        frame_count=duration_ms // 40,
        duration_ms=duration_ms,
        has_audio=True,
    )


def test_manifest_plans_120_second_chunks_with_five_second_context(tmp_path: Path):
    options = RunnerOptions()
    manifest = FullMatchManifest.create(
        job_id="job-1",
        source_uri="s3://football-media/uploads/video.mp4",
        options=options,
        source=_probe(tmp_path / "source.mp4"),
        proxy=_probe(tmp_path / "proxy.mp4"),
    )

    assert [(chunk.context_start_ms, chunk.end_ms) for chunk in manifest.chunks] == [
        (0, 120_000),
        (115_000, 235_000),
        (230_000, 250_000),
    ]
    assert [(chunk.output_start_ms, chunk.end_ms) for chunk in manifest.chunks] == [
        (0, 120_000),
        (120_000, 235_000),
        (235_000, 250_000),
    ]
    assert manifest.progress == 0


def test_manifest_store_replaces_atomically_and_ignores_uncommitted_partial(
    tmp_path: Path,
):
    manifest = FullMatchManifest.create(
        job_id="job-1",
        source_uri="s3://football-media/video.mp4",
        options=RunnerOptions(),
        source=_probe(tmp_path / "source.mp4", 1_000),
        proxy=_probe(tmp_path / "proxy.mp4", 1_000),
    )
    store = ManifestStore(tmp_path / "manifest.json")
    store.save(manifest)
    (tmp_path / "manifest.json.partial").write_text("{interrupted", encoding="utf-8")

    restored = store.load()

    assert restored == manifest
    assert store.path.read_text(encoding="utf-8").endswith("\n")
