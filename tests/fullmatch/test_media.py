import subprocess
from pathlib import Path

import pytest

import football_intelligence.fullmatch.media as media_module
from football_intelligence.fullmatch.media import (
    MAX_MATCH_DURATION_MS,
    MediaCancelled,
    MediaProbe,
    _media_probe_from_payload,
    build_proxy,
    localize_s3_source,
    parse_same_bucket_uri,
    probe_media,
    run_media_command,
    validate_full_decode,
    validate_source_media,
)


class StreamingStore:
    def __init__(self, chunks, *, failure: Exception | None = None):
        self.chunks = chunks
        self.failure = failure
        self.requested_key = None

    def iter_object(self, object_key, chunk_size=1024 * 1024):
        self.requested_key = object_key
        yield from self.chunks
        if self.failure:
            raise self.failure


def test_same_bucket_uri_returns_only_opaque_key():
    assert (
        parse_same_bucket_uri(
            "s3://football-media/uploads/opaque/source.mp4", "football-media"
        )
        == "uploads/opaque/source.mp4"
    )

    for unsafe in (
        "s3://another-bucket/uploads/opaque/source.mp4",
        "s3://football-media/uploads/../secret.mp4",
        "s3://football-media/uploads/%2e%2e/secret.mp4",
        "s3://football-media/uploads/source.mp4?versionId=secret",
        "file:///tmp/source.mp4",
    ):
        with pytest.raises(ValueError):
            parse_same_bucket_uri(unsafe, "football-media")


def test_localization_streams_to_atomic_final_file(tmp_path: Path):
    store = StreamingStore([b"first", b"second"])

    localized = localize_s3_source(
        store,
        "s3://football-media/uploads/opaque/source.mp4",
        bucket="football-media",
        destination_dir=tmp_path,
    )

    assert localized == tmp_path / "source.mp4"
    assert localized.read_bytes() == b"firstsecond"
    assert store.requested_key == "uploads/opaque/source.mp4"
    assert not (tmp_path / "source.partial").exists()


def test_failed_localization_leaves_no_visible_or_partial_source(tmp_path: Path):
    store = StreamingStore([b"partial"], failure=OSError("connection lost"))

    with pytest.raises(OSError, match="connection lost"):
        localize_s3_source(
            store,
            "s3://football-media/uploads/opaque/source.mp4",
            bucket="football-media",
            destination_dir=tmp_path,
        )

    assert not (tmp_path / "source.mp4").exists()
    assert not (tmp_path / "source.partial").exists()


def test_localization_cancellation_removes_partial_source(tmp_path: Path):
    store = StreamingStore([b"partial", b"more"])

    with pytest.raises(MediaCancelled):
        localize_s3_source(
            store,
            "s3://football-media/uploads/opaque/source.mp4",
            bucket="football-media",
            destination_dir=tmp_path,
            cancelled=lambda: True,
        )

    assert not (tmp_path / "source.mp4").exists()
    assert not (tmp_path / "source.partial").exists()


def test_media_command_can_be_cancelled_while_encoder_is_running():
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(MediaCancelled):
        run_media_command(
            ["python", "-c", "import time; time.sleep(5)"], cancelled=cancelled
        )


def test_routine_probe_is_non_exhaustive_and_marks_derived_count(
    tmp_path: Path, monkeypatch
):
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 320,
                "height": 180,
                "avg_frame_rate": "25/1",
            }
        ],
        "format": {"format_name": "mov,mp4", "duration": "2.0"},
    }
    captured: list[str] = []

    def fake_command(command, *, cancelled=None):
        del cancelled
        captured.extend(command)
        return subprocess.CompletedProcess(
            command, 0, stdout=__import__("json").dumps(payload), stderr=""
        )

    monkeypatch.setattr(media_module, "run_media_command", fake_command)

    probe = probe_media(tmp_path / "source.mp4")

    assert "-count_frames" not in captured
    assert probe.frame_count == 50
    assert probe.frame_count_estimated is True


def test_routine_probe_forwards_cancellation_to_process_helper(
    tmp_path: Path, monkeypatch
):
    def cancelled() -> bool:
        return True

    def cancelled_command(command, *, cancelled=None):
        del command
        assert cancelled is not None and cancelled()
        raise MediaCancelled("probe cancelled")

    monkeypatch.setattr(media_module, "run_media_command", cancelled_command)

    with pytest.raises(MediaCancelled, match="probe cancelled"):
        probe_media(tmp_path / "source.mp4", cancelled=cancelled)


def test_full_decode_uses_xerror_and_rejects_any_error_diagnostics(
    tmp_path: Path, monkeypatch
):
    captured: list[str] = []

    def diagnostic_command(command, *, cancelled=None):
        assert cancelled is not None and not cancelled()
        captured.extend(command)
        return subprocess.CompletedProcess(
            command, 0, stdout="", stderr="concealing damaged macroblock"
        )

    monkeypatch.setattr(media_module, "run_media_command", diagnostic_command)

    with pytest.raises(RuntimeError, match="decode diagnostics"):
        validate_full_decode(tmp_path / "annotated.mp4", cancelled=lambda: False)
    assert "-xerror" in captured


def test_full_decode_rejects_corrupted_real_media(tmp_path: Path):
    source = tmp_path / "valid.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=25:duration=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(source),
        ],
        check=True,
    )
    corrupt = tmp_path / "corrupt.mp4"
    body = source.read_bytes()
    corrupt.write_bytes(body[: len(body) // 2])

    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        validate_full_decode(corrupt)


def test_source_validation_rejects_duration_and_unsupported_container():
    valid = MediaProbe(
        path="source.mp4",
        container="mov,mp4,m4a,3gp,3g2,mj2",
        video_codec="h264",
        width=1920,
        height=1080,
        fps=50,
        frame_count=100,
        duration_ms=MAX_MATCH_DURATION_MS,
        has_audio=True,
    )

    validate_source_media(valid)
    with pytest.raises(ValueError, match="150 minutes"):
        validate_source_media(valid.model_copy(update={"duration_ms": MAX_MATCH_DURATION_MS + 1}))
    with pytest.raises(ValueError, match="container"):
        validate_source_media(valid.model_copy(update={"container": "avi"}))


def test_proxy_is_h264_capped_at_720p_25fps_and_preserves_audio(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
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
    )

    proxy = build_proxy(source, tmp_path / "proxy.mp4")
    metadata = probe_media(proxy)

    assert metadata.video_codec == "h264"
    assert (metadata.width, metadata.height) == (640, 360)
    assert metadata.fps == pytest.approx(25, abs=0.01)
    assert metadata.duration_ms == pytest.approx(2000, abs=50)
    assert metadata.has_audio is True
    assert not (tmp_path / "proxy.partial.mp4").exists()


def test_probe_falls_back_from_invalid_average_rate_and_derives_duration():
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "0/0",
                "r_frame_rate": "30000/1001",
                "nb_frames": "300",
                "duration": "N/A",
            }
        ],
        "format": {"format_name": "matroska", "duration": "N/A"},
    }

    probe = _media_probe_from_payload(Path("match.mkv"), payload)

    assert probe.fps == pytest.approx(29.970, abs=0.001)
    assert probe.frame_count == 300
    assert probe.duration_ms == pytest.approx(10_010, abs=1)


def test_probe_uses_average_rate_for_vfr_identity_and_counted_frames():
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "20/1",
                "r_frame_rate": "30/1",
                "nb_read_frames": "40",
                "duration": "2.0",
            }
        ],
        "format": {"format_name": "mov,mp4", "duration": "2.0"},
    }

    probe = _media_probe_from_payload(Path("vfr.mp4"), payload)

    assert probe.fps == 20
    assert probe.frame_count == 40
    assert probe.duration_ms == 2_000


@pytest.mark.parametrize("suffix", [".mkv", ".mov"])
def test_probe_supports_real_mkv_and_mov_with_derived_frame_identity(
    tmp_path: Path, suffix: str
):
    source = tmp_path / f"source{suffix}"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=24000/1001:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )

    probe = probe_media(source)

    validate_source_media(probe)
    assert probe.video_codec == "h264"
    assert probe.pixel_format == "yuv420p"
    assert probe.fps == pytest.approx(23.976, abs=0.001)
    assert probe.frame_count > 0 and probe.duration_ms > 0
