from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from football_intelligence.domain import (
    Artifact,
    CalibrationObservation,
    EventEvidence,
    EventReview,
    EventStatus,
    FootballEvent,
    FullMatchVideoMetadata,
    JobStage,
    ModelManifest,
    ProxyVideoMetadata,
    ScoreboardObservation,
    ScoreboardRegion,
    StageName,
    StageStatus,
    UploadSession,
    VideoMetadata,
)


def test_stage_contract_uses_stable_pipeline_values():
    assert [stage.value for stage in StageName] == [
        "upload_validation",
        "source_probe_proxy",
        "shot_classification",
        "ocr",
        "detection_tracking",
        "team_calibration",
        "action_spotting",
        "event_fusion",
        "heat_maps",
        "clips",
        "annotated_rendering",
    ]
    assert [status.value for status in StageStatus] == [
        "pending",
        "running",
        "completed",
        "failed",
        "stopped",
    ]
    assert [status.value for status in EventStatus] == ["candidate", "confirmed", "rejected"]


def test_full_match_models_round_trip_literal_contracts():
    reviewed_at = datetime(2026, 8, 18, 12, 30, tzinfo=UTC)
    region = ScoreboardRegion(x=0.04, y=0.03, width=0.24, height=0.08)
    observation = ScoreboardObservation(
        timestamp_ms=123_000,
        match_clock_ms=1_230_000,
        period=1,
        home_team="Home FC",
        away_team="Away FC",
        home_score=1,
        away_score=0,
        confidence=0.94,
        region=region,
        frame_index=3075,
    )
    calibration = CalibrationObservation(
        timestamp_ms=123_000,
        frame_index=3075,
        homography=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        reprojection_error_m=1.2,
    )
    artifact = Artifact(
        job_id="job-1",
        artifact_type="proxy",
        uri="s3://football/jobs/job-1/proxy-v1.mp4",
        version="v1",
        content_type="video/mp4",
        sha256="a" * 64,
        size_bytes=10_000,
    )
    upload = UploadSession(
        id="upload-1",
        job_id="job-1",
        object_key="uploads/upload-1/source.mp4",
        original_filename="match.mp4",
        size_bytes=16 * 1024 * 1024,
        part_size_bytes=16 * 1024 * 1024,
        checksum_sha256="b" * 64,
    )
    review = EventReview(
        event_id="event-1",
        reviewer_id="operator-1",
        decision=EventStatus.CONFIRMED,
        note="Scoreboard and replay agree.",
        reviewed_at=reviewed_at,
    )
    manifest = ModelManifest(
        model_name="soccer-calF",
        version="1.0.0",
        source="https://example.test/models/soccer-calf",
        license="MIT",
        weight_sha256="c" * 64,
        classes=["goal", "shot"],
        runtime="onnxruntime",
        device="cuda",
        benchmark="held-out-v1",
        limitations="Candidate generation only.",
    )
    stage = JobStage(job_id="job-1", stage=StageName.OCR)

    assert observation.model_dump() == {
        "timestamp_ms": 123_000,
        "match_clock_ms": 1_230_000,
        "period": 1,
        "home_team": "Home FC",
        "away_team": "Away FC",
        "home_score": 1,
        "away_score": 0,
        "confidence": 0.94,
        "region": {"x": 0.04, "y": 0.03, "width": 0.24, "height": 0.08},
        "frame_index": 3075,
    }
    assert calibration.reprojection_error_m == 1.2
    assert artifact.size_bytes == 10_000
    assert upload.part_size_bytes == 16 * 1024 * 1024
    assert review.reviewed_at == reviewed_at
    assert manifest.classes == ["goal", "shot"]
    assert stage.status is StageStatus.PENDING


@pytest.mark.parametrize(
    "region",
    [
        {"x": -0.01, "y": 0.03, "width": 0.24, "height": 0.08},
        {"x": 0.80, "y": 0.93, "width": 0.24, "height": 0.08},
    ],
)
def test_scoreboard_region_rejects_coordinates_outside_normalized_frame(region):
    with pytest.raises(ValidationError):
        ScoreboardRegion(**region)


def test_job_stage_rejects_an_unknown_stage_and_invalid_checkpoint():
    with pytest.raises(ValidationError):
        JobStage(job_id="job-1", stage="render")
    with pytest.raises(ValidationError):
        JobStage(job_id="job-1", stage=StageName.OCR, checkpoint_ms=-1)


def test_job_stage_allows_only_explicit_status_transitions():
    pending = JobStage(job_id="job-1", stage=StageName.OCR)
    running = pending.transition_to(StageStatus.RUNNING)
    stopped = running.transition_to(StageStatus.STOPPED)

    assert running.status is StageStatus.RUNNING
    assert stopped.status is StageStatus.STOPPED
    with pytest.raises(ValueError, match="cannot transition"):
        pending.transition_to(StageStatus.COMPLETED)
    with pytest.raises(ValueError, match="cannot transition"):
        stopped.transition_to(StageStatus.COMPLETED)


@pytest.mark.parametrize(
    ("size_bytes", "part_size_bytes"),
    [
        (12 * 1024**3 + 1, 16 * 1024 * 1024),
        (16 * 1024 * 1024, 8 * 1024 * 1024),
    ],
)
def test_upload_session_enforces_full_match_quota_and_part_size(size_bytes, part_size_bytes):
    with pytest.raises(ValidationError):
        UploadSession(
            id="upload-1",
            job_id="job-1",
            object_key="uploads/upload-1/source.mp4",
            original_filename="match.mp4",
            size_bytes=size_bytes,
            part_size_bytes=part_size_bytes,
            checksum_sha256="b" * 64,
        )


def test_football_event_round_trips_full_match_metadata_without_changing_legacy_fields():
    review = EventReview(
        event_id="event-1",
        reviewer_id="operator-1",
        decision=EventStatus.CONFIRMED,
        note="Scoreboard and replay agree.",
        reviewed_at=datetime(2026, 8, 18, 12, 30, tzinfo=UTC),
    )
    event = FootballEvent(
        id="event-1",
        job_id="job-1",
        event_type="goal",
        start_ms=2_400_000,
        end_ms=2_405_000,
        game_time="40:00",
        team="team_1",
        player="unknown",
        description="Score increased after a goal candidate.",
        confidence=0.98,
        evidence=[EventEvidence(kind="score_change", value="1-0 to 2-0", confidence=0.99)],
        source=["scoreboard.ocr", "action.spotter"],
        needs_review=False,
        status=EventStatus.CONFIRMED,
        period=1,
        match_clock_ms=2_400_000,
        score_transition="1-0 to 2-0",
        producer_version="soccer-calf@1.0.0",
        review=review,
        original_model_output={
            "action.spotter": {"labels": ["goal"], "scores": [0.98]},
            "scoreboard.ocr": {"text": "Home FC 2-0 Away FC"},
        },
    )

    assert event.model_dump()["event_type"] == "goal"
    assert event.status is EventStatus.CONFIRMED
    assert event.period == 1
    assert event.match_clock_ms == 2_400_000
    assert event.score_transition == "1-0 to 2-0"
    assert event.producer_version == "soccer-calf@1.0.0"
    assert event.review == review
    assert event.original_model_output == {
        "action.spotter": {"labels": ["goal"], "scores": [0.98]},
        "scoreboard.ocr": {"text": "Home FC 2-0 Away FC"},
    }


def test_event_review_rejects_a_candidate_decision():
    with pytest.raises(ValidationError):
        EventReview(
            event_id="event-1",
            reviewer_id="operator-1",
            decision=EventStatus.CANDIDATE,
            note="Needs more evidence.",
            reviewed_at=datetime(2026, 8, 18, 12, 30, tzinfo=UTC),
        )


def test_full_match_input_metadata_accepts_the_150_minute_boundary():
    metadata = FullMatchVideoMetadata(
        source_path="/matches/final.mp4",
        width=1920,
        height=1080,
        fps=25,
        frame_count=225_000,
        duration_ms=9_000_000,
        codec="h264",
    )

    assert metadata.duration_ms == 9_000_000


def test_full_match_input_metadata_rejects_duration_over_150_minutes():
    with pytest.raises(ValidationError):
        FullMatchVideoMetadata(
            source_path="/matches/final.mp4",
            width=1920,
            height=1080,
            fps=25,
            frame_count=225_001,
            duration_ms=9_000_001,
            codec="h264",
        )


def test_proxy_metadata_accepts_1080p_at_25_fps():
    metadata = ProxyVideoMetadata(
        source_path="/proxies/final.mp4",
        width=1920,
        height=1080,
        fps=25,
        frame_count=225_000,
        duration_ms=9_000_000,
        codec="h264",
    )

    assert metadata.height == 1080
    assert metadata.fps == 25


@pytest.mark.parametrize(
    ("height", "fps"),
    [
        (1081, 25),
        (1080, 25.01),
    ],
)
def test_proxy_metadata_rejects_values_above_the_1080p_25_fps_caps(height, fps):
    with pytest.raises(ValidationError):
        ProxyVideoMetadata(
            source_path="/proxies/final.mp4",
            width=1920,
            height=height,
            fps=fps,
            frame_count=225_000,
            duration_ms=9_000_000,
            codec="h264",
        )


def test_legacy_video_metadata_remains_unrestricted_for_existing_pipeline_compatibility():
    metadata = VideoMetadata(
        source_path="/clips/source.mp4",
        width=3840,
        height=2160,
        fps=50,
        frame_count=450_001,
        duration_ms=9_000_001,
        codec="h264",
    )

    assert metadata.duration_ms == 9_000_001
