"""Bounded, auditable scoreboard OCR and temporal consensus."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from pydantic import BaseModel, Field

from football_intelligence.domain import (
    EventEvidence,
    FootballEvent,
    ScoreboardObservation,
    ScoreboardRegion,
)


class OcrResult(BaseModel):
    """Raw OCR output retained as evidence, including low-confidence reads."""

    text: str
    confidence: float = Field(ge=0, le=1)


class ParsedScoreboard(ScoreboardObservation):
    raw_text: str
    raw_confidence: float = Field(ge=0, le=1)


class OcrEngine(Protocol):
    def read(self, frame: np.ndarray | None, region: ScoreboardRegion) -> OcrResult: ...


class FakeOcrEngine:
    """Deterministic, finite OCR adapter for tests."""

    def __init__(self, results: Sequence[OcrResult]) -> None:
        self._results = deque(results)
        self.model_name = "finite-test-results"
        self.version = "1"
        self.model_sha256 = "0" * 64
        self.producer = "ocr.fake"

    def read(self, frame: np.ndarray | None, region: ScoreboardRegion) -> OcrResult:
        del frame, region
        if not self._results:
            return OcrResult(text="", confidence=0)
        return self._results.popleft()


class TesseractCliOcrEngine:
    """Tesseract CLI adapter that only sends the configured frame crop."""

    def __init__(
        self,
        tessdata_dir: str | Path,
        language: str = "eng",
        *,
        executable: str = "tesseract",
    ) -> None:
        self._tessdata_dir = str(tessdata_dir)
        self._language = language
        self._executable = executable
        self.model_name = language
        self.version = self._measure_version()
        self.model_sha256 = self._hash_model(Path(tessdata_dir) / f"{language}.traineddata")
        self.producer = "ocr.tesseract"

    def _measure_version(self) -> str:
        result = subprocess.run(
            [self._executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        first_line = (result.stdout or result.stderr).splitlines()[0]
        match = re.search(r"(?:tesseract\s+)?(?P<version>\d+(?:\.\d+)+)", first_line)
        if match is None:
            raise RuntimeError("could not measure Tesseract version")
        return match.group("version")

    @staticmethod
    def _hash_model(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def read(self, frame: np.ndarray | None, region: ScoreboardRegion) -> OcrResult:
        if frame is None:
            return OcrResult(text="", confidence=0)
        height, width = frame.shape[:2]
        x1 = round(region.x * width)
        y1 = round(region.y * height)
        x2 = round((region.x + region.width) * width)
        y2 = round((region.y + region.height) * height)
        crop = frame[y1:y2, x1:x2]
        encoded, image = cv2.imencode(".png", crop)
        if not encoded:
            return OcrResult(text="", confidence=0)
        result = subprocess.run(
            [
                self._executable,
                "stdin",
                "stdout",
                "--tessdata-dir",
                self._tessdata_dir,
                "-l",
                self._language,
                "--psm",
                "6",
                "-c",
                "tessedit_create_tsv=1",
            ],
            input=image.tobytes(),
            capture_output=True,
            check=True,
            timeout=30,
        )
        words: list[str] = []
        confidences: list[float] = []
        for line in result.stdout.decode("utf-8", errors="replace").splitlines()[1:]:
            columns = line.split("\t", 11)
            if len(columns) != 12 or not columns[11].strip():
                continue
            words.append(columns[11].strip())
            try:
                confidence = float(columns[10])
            except ValueError:
                continue
            if confidence >= 0:
                confidences.append(confidence / 100)
        average = sum(confidences) / len(confidences) if confidences else 0
        return OcrResult(text=" ".join(words), confidence=min(1, average))


class ScoreboardParser:
    """Parse common clock/score layouts without guessing missing fields."""

    _clock = re.compile(r"\b(?P<minutes>\d{1,3}):(?P<seconds>[0-5]\d)\b")
    _score = re.compile(r"(?<!\d)(?P<home>\d{1,2})\s*[-:]\s*(?P<away>\d{1,2})(?!\d)")

    def parse(
        self,
        result: OcrResult,
        *,
        timestamp_ms: int,
        frame_index: int,
        region: ScoreboardRegion,
        known_teams: tuple[str, str] | None = None,
        known_clock_ms: int | None = None,
    ) -> ParsedScoreboard | None:
        clock = self._clock.search(result.text)
        scores = [
            match
            for match in self._score.finditer(result.text)
            if clock is None or match.span() != clock.span()
        ]
        if not scores:
            return None
        score = scores[0]
        # A clock is best-effort: some real scoreboards render it unreadably or
        # not at all. The consensus carries the last known clock so a score/team
        # read still yields a usable reading; otherwise the video timestamp is a
        # monotonic fallback clock.
        if clock is not None:
            match_clock_ms = (
                int(clock.group("minutes")) * 60 + int(clock.group("seconds"))
            ) * 1_000
        elif known_clock_ms is not None:
            match_clock_ms = known_clock_ms
        else:
            match_clock_ms = timestamp_ms
        prefix_words = re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", result.text[: score.start()])
        suffix_words = re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", result.text[score.end() :])
        # Team tokens are best-effort. Once teams are already known they are
        # carried by the consensus, so a single read that misses the team
        # tokens (or the far team) still yields a usable score/clock reading.
        home_team = prefix_words[-1] if prefix_words else None
        away_team = suffix_words[0] if suffix_words else None
        if home_team is None or away_team is None:
            if known_teams is not None:
                home_team = home_team or known_teams[0]
                away_team = away_team or known_teams[1]
            elif home_team is None and away_team is None:
                return None
            else:
                # Seed from a single readable token; the missing side starts as
                # an unknown placeholder that the consensus refines later.
                if home_team is None:
                    home_team = "unknown"
                if away_team is None:
                    away_team = "unknown"
        return ParsedScoreboard(
            timestamp_ms=timestamp_ms,
            match_clock_ms=match_clock_ms,
            period=1,
            home_team=home_team,
            away_team=away_team,
            home_score=int(score.group("home")),
            away_score=int(score.group("away")),
            confidence=result.confidence,
            region=region,
            frame_index=frame_index,
            raw_text=result.text,
            raw_confidence=result.confidence,
        )


class ScoreboardConsensus:
    """Monotonic clock and bounded rolling score consensus for candidates.

    Team names are learned once and then carried by the consensus, so a single
    read that misses the team tokens (or the far team) still yields a usable
    score/clock reading. A bounded number of consecutive missed OCR reads does
    not reset a developing score change; only a sustained loss of the candidate
    score abandons it. A stable valid score increase emits exactly one
    ``score_change_candidate`` with ``needs_review=True``.
    """

    def __init__(
        self,
        job_id: str,
        stable_score_ms: int = 5_000,
        *,
        producer: str = "ocr.tesseract",
        producer_version: str = "unknown",
        state: ConsensusState | None = None,
        max_pending_gap_ms: int = 3_000,
        max_consecutive_misses: int = 3,
    ) -> None:
        self._job_id = job_id
        self._stable_score_ms = stable_score_ms
        self._max_pending_gap_ms = max_pending_gap_ms
        self._max_consecutive_misses = max_consecutive_misses
        self._producer = producer
        self._producer_version = producer_version
        self._period = 1
        self._home_team: str | None = None
        self._away_team: str | None = None
        self._last_clock_ms: int | None = None
        self._accepted_score: tuple[int, int] | None = None
        self._pending_score: tuple[int, int] | None = None
        self._pending_since_ms: int | None = None
        self._pending_last_ms: int | None = None
        self._pending_reads: list[ParsedScoreboard] = []
        self._pending_misses = 0
        if state is not None:
            self._period = state.period
            self._last_clock_ms = state.last_clock_ms
            self._home_team = state.home_team
            self._away_team = state.away_team
            self._accepted_score = state.accepted_score
            self._pending_score = state.pending_score
            self._pending_since_ms = state.pending_since_ms
            self._pending_last_ms = state.pending_last_ms
            self._pending_reads = list(state.pending_reads)
            self._pending_misses = state.pending_misses

    def known_teams(self) -> tuple[str, str] | None:
        """Return the current best-known team tokens, if any are known."""
        if self._home_team is None or self._away_team is None:
            return None
        return (self._home_team, self._away_team)

    def known_clock_ms(self) -> int | None:
        """Return the last accepted match clock in ms, if any."""
        return self._last_clock_ms

    def seed(self, observation: ScoreboardObservation) -> None:
        """Restore accepted state from a durable completed-chunk observation."""
        self._period = observation.period
        self._last_clock_ms = observation.match_clock_ms
        self._home_team = observation.home_team
        self._away_team = observation.away_team
        self._accepted_score = (observation.home_score, observation.away_score)
        self._clear_pending()

    def snapshot(self) -> ConsensusState:
        return ConsensusState(
            period=self._period,
            last_clock_ms=self._last_clock_ms,
            home_team=self._home_team,
            away_team=self._away_team,
            accepted_score=self._accepted_score,
            pending_score=self._pending_score,
            pending_since_ms=self._pending_since_ms,
            pending_last_ms=self._pending_last_ms,
            pending_reads=self._pending_reads,
            pending_misses=self._pending_misses,
        )

    def observe_missing(self, timestamp_ms: int) -> None:
        """Record a missed OCR read without resetting a developing change.

        A bounded number of consecutive misses is tolerated; only after the
        candidate score has been unseen for too long do we abandon it. If no
        change is pending there is nothing to clear.
        """
        if self._pending_score is None:
            return
        self._pending_misses += 1
        self._pending_last_ms = timestamp_ms
        if self._pending_misses > self._max_consecutive_misses:
            self._clear_pending()

    def observe(
        self, parsed: ParsedScoreboard
    ) -> tuple[ScoreboardObservation | None, FootballEvent | None]:
        # Learn team tokens once; thereafter carry them so a read that misses
        # the team OCR still counts toward the score/clock consensus. A real
        # token replaces an "unknown" placeholder as soon as it is readable.
        if self._home_team is None or (
            self._home_team == "unknown" and parsed.home_team != "unknown"
        ):
            self._home_team = parsed.home_team
        if self._away_team is None or (
            self._away_team == "unknown" and parsed.away_team != "unknown"
        ):
            self._away_team = parsed.away_team
        if self._last_clock_ms is not None and parsed.match_clock_ms < self._last_clock_ms:
            halftime_reset = (
                self._last_clock_ms >= 40 * 60 * 1_000
                and parsed.match_clock_ms <= 15 * 60 * 1_000
                and self._last_clock_ms - parsed.match_clock_ms >= 10 * 60 * 1_000
            )
            if not halftime_reset:
                return None, None
            self._period += 1
        self._last_clock_ms = parsed.match_clock_ms
        reading = parsed.model_copy(
            update={
                "period": self._period,
                "home_team": self._home_team,
                "away_team": self._away_team,
            }
        )
        score = (reading.home_score, reading.away_score)
        if self._accepted_score is None:
            self._accepted_score = score
            return self._public(reading), None
        if score == self._accepted_score:
            self._clear_pending()
            return self._public(reading), None
        if score[0] < self._accepted_score[0] or score[1] < self._accepted_score[1]:
            return None, None
        if (
            score != self._pending_score
            or self._pending_since_ms is None
            or reading.timestamp_ms - self._pending_last_ms > self._max_pending_gap_ms
        ):
            self._pending_score = score
            self._pending_since_ms = reading.timestamp_ms
            self._pending_last_ms = reading.timestamp_ms
            self._pending_reads = [reading]
            self._pending_misses = 0
            return None, None
        self._pending_last_ms = reading.timestamp_ms
        self._pending_reads.append(reading)
        self._pending_misses = 0
        assert self._pending_since_ms is not None
        if reading.timestamp_ms - self._pending_since_ms < self._stable_score_ms:
            return None, None
        previous = self._accepted_score
        self._accepted_score = score
        supporting_reads = list(self._pending_reads)
        self._clear_pending()
        transition = f"{previous[0]}-{previous[1]} -> {score[0]}-{score[1]}"
        event = FootballEvent(
            job_id=self._job_id,
            event_type="score_change_candidate",
            start_ms=reading.timestamp_ms,
            end_ms=reading.timestamp_ms,
            game_time=(
                f"{reading.match_clock_ms // 60_000:02d}:"
                f"{reading.match_clock_ms // 1_000 % 60:02d}"
            ),
            description=f"Scoreboard changed {transition}",
            confidence=reading.confidence,
            evidence=[
                EventEvidence(
                    kind="scoreboard_ocr",
                    value=transition,
                    confidence=reading.confidence,
                    frame_refs=[reading.frame_index],
                    detail=reading.raw_text,
                )
            ],
            source=[f"{self._producer}.consensus"],
            frame_refs=[reading.frame_index],
            needs_review=True,
            period=self._period,
            match_clock_ms=reading.match_clock_ms,
            score_transition=transition,
            producer_version=self._producer_version,
            original_model_output={
                "raw_text": reading.raw_text,
                "raw_confidence": reading.raw_confidence,
                "supporting_reads": [
                    {
                        "timestamp_ms": item.timestamp_ms,
                        "frame_index": item.frame_index,
                        "raw_text": item.raw_text,
                        "raw_confidence": item.raw_confidence,
                    }
                    for item in supporting_reads
                ],
            },
        )
        event.evidence[0].frame_refs = [item.frame_index for item in supporting_reads]
        return self._public(reading), event

    def _clear_pending(self) -> None:
        self._pending_score = None
        self._pending_since_ms = None
        self._pending_last_ms = None
        self._pending_reads = []
        self._pending_misses = 0

    @staticmethod
    def _public(reading: ParsedScoreboard) -> ScoreboardObservation:
        return ScoreboardObservation.model_validate(
            reading.model_dump(exclude={"raw_text", "raw_confidence"})
        )


class ConsensusState(BaseModel):
    period: int = Field(default=1, ge=1)
    last_clock_ms: int | None = Field(default=None, ge=0)
    home_team: str | None = None
    away_team: str | None = None
    accepted_score: tuple[int, int] | None = None
    pending_score: tuple[int, int] | None = None
    pending_since_ms: int | None = Field(default=None, ge=0)
    pending_last_ms: int | None = Field(default=None, ge=0)
    pending_reads: list[ParsedScoreboard] = Field(default_factory=list)
    pending_misses: int = Field(default=0, ge=0)
