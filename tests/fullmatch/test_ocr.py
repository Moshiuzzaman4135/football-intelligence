from collections import deque

import cv2
import numpy as np
import pytest

from football_intelligence.domain import EventStatus, ScoreboardRegion
from football_intelligence.fullmatch.ocr import (
    FakeOcrEngine,
    OcrResult,
    ScoreboardConsensus,
    ScoreboardParser,
    TesseractCliOcrEngine,
)

REGION = ScoreboardRegion(x=0, y=0, width=1, height=0.2)


def test_scoreboard_parser_accepts_common_clock_score_formats_and_raw_evidence():
    parser = ScoreboardParser()

    parsed = parser.parse(
        OcrResult(text="ARS 2 - 1 CHE   67:04", confidence=0.88),
        timestamp_ms=1_000,
        frame_index=25,
        region=REGION,
    )

    assert parsed is not None
    assert parsed.match_clock_ms == 4_024_000
    assert (parsed.home_team, parsed.away_team) == ("ARS", "CHE")
    assert (parsed.home_score, parsed.away_score) == (2, 1)
    assert parsed.raw_text == "ARS 2 - 1 CHE   67:04"
    assert parsed.raw_confidence == 0.88

    compact = parser.parse(
        OcrResult(text="12:34 HOME 0:0 AWAY", confidence=0.75),
        timestamp_ms=2_000,
        frame_index=50,
        region=REGION,
    )
    assert compact is not None
    assert compact.match_clock_ms == 754_000
    assert (compact.home_score, compact.away_score) == (0, 0)


def test_parser_returns_unknown_reading_instead_of_inventing_score():
    parsed = ScoreboardParser().parse(
        OcrResult(text="LIVE SPORTS", confidence=0.42),
        timestamp_ms=1_000,
        frame_index=25,
        region=REGION,
    )

    assert parsed is None


def test_consensus_requires_five_seconds_for_score_change_and_emits_candidate():
    parser = ScoreboardParser()
    consensus = ScoreboardConsensus(job_id="job-1", stable_score_ms=5_000)
    initial = parser.parse(
        OcrResult(text="AAA 0-0 BBB 10:00", confidence=0.9),
        timestamp_ms=0,
        frame_index=0,
        region=REGION,
    )
    assert initial is not None
    accepted, event = consensus.observe(initial)
    assert accepted is not None
    assert event is None

    for second in range(10, 15):
        reading = parser.parse(
            OcrResult(text=f"AAA 1-0 BBB 10:{second:02d}", confidence=0.8),
            timestamp_ms=(second - 10) * 1_000 + 10_000,
            frame_index=second * 25,
            region=REGION,
        )
        assert reading is not None
        accepted, event = consensus.observe(reading)
        assert accepted is None
        assert event is None

    confirmed = parser.parse(
        OcrResult(text="AAA 1-0 BBB 10:15", confidence=0.82),
        timestamp_ms=15_000,
        frame_index=375,
        region=REGION,
    )
    assert confirmed is not None
    accepted, event = consensus.observe(confirmed)

    assert accepted is not None
    assert (accepted.home_score, accepted.away_score) == (1, 0)
    assert event is not None
    assert event.event_type == "score_change_candidate"
    assert event.status is EventStatus.CANDIDATE
    assert event.needs_review is True
    assert event.score_transition == "0-0 -> 1-0"
    assert event.source == ["ocr.tesseract.consensus"]
    assert event.original_model_output["raw_text"] == "AAA 1-0 BBB 10:15"
    assert event.original_model_output["raw_confidence"] == 0.82
    assert len(event.original_model_output["supporting_reads"]) == 6
    assert event.evidence[0].frame_refs == [250, 275, 300, 325, 350, 375]


def test_consensus_rejects_clock_reversal_but_allows_halftime_reset():
    parser = ScoreboardParser()
    consensus = ScoreboardConsensus(job_id="job-1")

    def reading(text: str, timestamp_ms: int):
        parsed = parser.parse(
            OcrResult(text=text, confidence=0.9),
            timestamp_ms=timestamp_ms,
            frame_index=timestamp_ms // 40,
            region=REGION,
        )
        assert parsed is not None
        return parsed

    first, _ = consensus.observe(reading("AAA 0-0 BBB 45:00", 0))
    reversed_clock, _ = consensus.observe(reading("AAA 0-0 BBB 44:59", 1_000))
    second_half, _ = consensus.observe(reading("AAA 0-0 BBB 00:05", 2_000))

    assert first is not None and first.period == 1
    assert reversed_clock is None
    assert second_half is not None and second_half.period == 2
    assert second_half.match_clock_ms == 5_000


def test_fake_ocr_engine_is_deterministic_and_bounded_to_supplied_results():
    expected = deque(
        [
            OcrResult(text="AAA 0-0 BBB 00:01", confidence=0.9),
            OcrResult(text="AAA 0-0 BBB 00:02", confidence=0.8),
        ]
    )
    engine = FakeOcrEngine(list(expected))

    assert engine.read(None, REGION) == expected[0]
    assert engine.read(None, REGION) == expected[1]
    assert engine.read(None, REGION) == OcrResult(text="", confidence=0)


def test_bounded_missed_read_does_not_reset_developing_score_change():
    parser = ScoreboardParser()
    consensus = ScoreboardConsensus(job_id="job-1", stable_score_ms=5_000)

    def parse(score: str, timestamp_ms: int):
        parsed = parser.parse(
            OcrResult(text=f"AAA {score} BBB 10:{timestamp_ms // 1000:02d}", confidence=0.9),
            timestamp_ms=timestamp_ms,
            frame_index=timestamp_ms // 40,
            region=REGION,
        )
        assert parsed is not None
        return parsed

    consensus.observe(parse("0-0", 0))
    # Pending window starts at t=1 s (score 1-0).
    for timestamp_ms in (1_000, 2_000, 3_000):
        consensus.observe(parse("1-0", timestamp_ms))
    # Misses at 4 s and 5 s are tolerated (must NOT reset the window).
    consensus.observe_missing(4_000)
    consensus.observe_missing(5_000)
    accepted, event = consensus.observe(parse("1-0", 6_000))

    # The window spans 1s..6s = 5s despite the two misses, so it emits here.
    # (A strict reset would have restarted the window at 6s and needed ~11s.)
    assert accepted is not None
    assert event is not None
    assert [item["timestamp_ms"] for item in event.original_model_output["supporting_reads"]] == [
        1_000,
        2_000,
        3_000,
        6_000,
    ]


def test_too_many_consecutive_misses_abandon_pending_change():
    parser = ScoreboardParser()
    consensus = ScoreboardConsensus(
        job_id="job-1", stable_score_ms=5_000, max_consecutive_misses=2
    )

    def parse(score: str, timestamp_ms: int):
        parsed = parser.parse(
            OcrResult(text=f"AAA {score} BBB 10:{timestamp_ms // 1000:02d}", confidence=0.9),
            timestamp_ms=timestamp_ms,
            frame_index=timestamp_ms // 40,
            region=REGION,
        )
        assert parsed is not None
        return parsed

    consensus.observe(parse("0-0", 0))
    consensus.observe(parse("1-0", 1_000))
    # Three consecutive misses with max_consecutive_misses=2 abandon pending.
    consensus.observe_missing(2_000)
    consensus.observe_missing(3_000)
    consensus.observe_missing(4_000)
    # A fresh 1-0 must re-establish a new window before it can be accepted.
    for timestamp_ms in (5_000, 6_000, 7_000, 8_000, 9_000):
        accepted, event = consensus.observe(parse("1-0", timestamp_ms))
        assert accepted is None and event is None

    accepted, event = consensus.observe(parse("1-0", 10_000))
    assert event is not None
    # Supporting reads start fresh after the abandonment.
    assert [item["timestamp_ms"] for item in event.original_model_output["supporting_reads"]] == [
        5_000,
        6_000,
        7_000,
        8_000,
        9_000,
        10_000,
    ]


def test_team_token_miss_keeps_known_teams_and_still_accepts_score_change():
    parser = ScoreboardParser()
    consensus = ScoreboardConsensus(job_id="job-1", stable_score_ms=5_000)

    def parse(text: str, timestamp_ms: int):
        parsed = parser.parse(
            OcrResult(text=text, confidence=0.9),
            timestamp_ms=timestamp_ms,
            frame_index=timestamp_ms // 40,
            region=REGION,
            known_teams=consensus.known_teams(),
        )
        assert parsed is not None
        return parsed

    initial = parser.parse(
        OcrResult(text="AAA 0-0 BBB 10:00", confidence=0.9),
        timestamp_ms=0,
        frame_index=0,
        region=REGION,
    )
    assert initial is not None
    accepted, _ = consensus.observe(initial)
    assert accepted is not None
    assert consensus.known_teams() == ("AAA", "BBB")

    # A read that misses the far team token still yields a usable reading.
    for second in range(1, 6):
        text = f"AAA 1-0 10:{second:02d}" if second % 2 == 0 else f"1-0 BBB 10:{second:02d}"
        reading = parse(text, second * 1_000)
        consensus.observe(reading)

    _, event = consensus.observe(parse("AAA 1-0 BBB 10:06", 6_000))

    assert event is not None
    assert event.score_transition == "0-0 -> 1-0"
    assert event.original_model_output["supporting_reads"]


def test_score_regression_is_rejected_and_does_not_emit():
    parser = ScoreboardParser()
    consensus = ScoreboardConsensus(job_id="job-1", stable_score_ms=5_000)
    initial = parser.parse(
        OcrResult(text="AAA 1-0 BBB 10:00", confidence=0.9),
        timestamp_ms=0,
        frame_index=0,
        region=REGION,
    )
    assert initial is not None
    consensus.observe(initial)
    regression = parser.parse(
        OcrResult(text="AAA 0-0 BBB 10:01", confidence=0.9),
        timestamp_ms=1_000,
        frame_index=25,
        region=REGION,
    )
    assert regression is not None
    accepted, event = consensus.observe(regression)
    assert accepted is None and event is None


def test_consensus_state_round_trip_preserves_pending_window_and_producer():
    parser = ScoreboardParser()
    consensus = ScoreboardConsensus(job_id="job-1", producer="ocr.fake", producer_version="2")
    for text, timestamp in (("AAA 0-0 BBB 10:00", 0), ("AAA 1-0 BBB 10:01", 1_000)):
        parsed = parser.parse(
            OcrResult(text=text, confidence=0.9),
            timestamp_ms=timestamp,
            frame_index=timestamp // 40,
            region=REGION,
        )
        assert parsed is not None
        consensus.observe(parsed)

    restored = ScoreboardConsensus(
        job_id="job-1",
        producer="ocr.fake",
        producer_version="2",
        state=consensus.snapshot(),
    )

    assert restored.snapshot() == consensus.snapshot()
    event = None
    for timestamp in (2_000, 3_000, 4_000, 5_000, 6_000):
        parsed = parser.parse(
            OcrResult(text=f"AAA 1-0 BBB 10:0{timestamp // 1000}", confidence=0.9),
            timestamp_ms=timestamp,
            frame_index=timestamp // 40,
            region=REGION,
        )
        assert parsed is not None
        _, event = restored.observe(parsed)
    assert event is not None
    assert event.source == ["ocr.fake.consensus"]
    assert event.producer_version == "2"


@pytest.mark.integration
def test_tesseract_cli_reads_only_the_manual_scoreboard_crop():
    frame = np.full((240, 800, 3), 255, dtype=np.uint8)
    cv2.putText(
        frame,
        "AAA 1-0 BBB 12:34",
        (20, 70),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.4,
        (0, 0, 0),
        3,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        "TEXT OUTSIDE ROI",
        (20, 200),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 0, 0),
        2,
        cv2.LINE_AA,
    )
    engine = TesseractCliOcrEngine(
        "/usr/share/tesseract-ocr/5/tessdata_fast"
    )

    result = engine.read(frame, ScoreboardRegion(x=0, y=0, width=1, height=0.4))

    assert "OUTSIDE" not in result.text
    assert "AAA" in result.text and ":" in result.text
    assert 0 < result.confidence <= 1
