"""Bounded, auditable scoreboard OCR and temporal consensus."""

from __future__ import annotations

import re
import subprocess
from collections import deque
from collections.abc import Sequence
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

    def read(self, frame: np.ndarray | None, region: ScoreboardRegion) -> OcrResult:
        del frame, region
        if not self._results:
            return OcrResult(text="", confidence=0)
        return self._results.popleft()


class TesseractCliOcrEngine:
    """Tesseract CLI adapter that only sends the configured frame crop."""

    def __init__(self, tessdata_dir: str, language: str = "eng") -> None:
        self._tessdata_dir = tessdata_dir
        self._language = language

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
                "tesseract",
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
    ) -> ParsedScoreboard | None:
        clock = self._clock.search(result.text)
        if clock is None:
            return None
        scores = [
            match
            for match in self._score.finditer(result.text)
            if match.span() != clock.span()
        ]
        if not scores:
            return None
        score = scores[0]
        prefix_words = re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", result.text[: score.start()])
        suffix_words = re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", result.text[score.end() :])
        if not prefix_words or not suffix_words:
            return None
        return ParsedScoreboard(
            timestamp_ms=timestamp_ms,
            match_clock_ms=(
                int(clock.group("minutes")) * 60 + int(clock.group("seconds"))
            )
            * 1_000,
            period=1,
            home_team=prefix_words[-1],
            away_team=suffix_words[0],
            home_score=int(score.group("home")),
            away_score=int(score.group("away")),
            confidence=result.confidence,
            region=region,
            frame_index=frame_index,
            raw_text=result.text,
            raw_confidence=result.confidence,
        )


class ScoreboardConsensus:
    """Monotonic clock and five-second score consensus for candidate events."""

    def __init__(self, job_id: str, stable_score_ms: int = 5_000) -> None:
        self._job_id = job_id
        self._stable_score_ms = stable_score_ms
        self._period = 1
        self._last_clock_ms: int | None = None
        self._accepted_score: tuple[int, int] | None = None
        self._pending_score: tuple[int, int] | None = None
        self._pending_since_ms: int | None = None

    def seed(self, observation: ScoreboardObservation) -> None:
        """Restore accepted state from a durable completed-chunk observation."""
        self._period = observation.period
        self._last_clock_ms = observation.match_clock_ms
        self._accepted_score = (observation.home_score, observation.away_score)
        self._pending_score = None
        self._pending_since_ms = None

    def observe(
        self, parsed: ParsedScoreboard
    ) -> tuple[ScoreboardObservation | None, FootballEvent | None]:
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
        reading = parsed.model_copy(update={"period": self._period})
        score = (reading.home_score, reading.away_score)
        if self._accepted_score is None:
            self._accepted_score = score
            return self._public(reading), None
        if score == self._accepted_score:
            self._pending_score = None
            self._pending_since_ms = None
            return self._public(reading), None
        if score[0] < self._accepted_score[0] or score[1] < self._accepted_score[1]:
            return None, None
        if score != self._pending_score:
            self._pending_score = score
            self._pending_since_ms = reading.timestamp_ms
            return None, None
        assert self._pending_since_ms is not None
        if reading.timestamp_ms - self._pending_since_ms < self._stable_score_ms:
            return None, None
        previous = self._accepted_score
        self._accepted_score = score
        self._pending_score = None
        self._pending_since_ms = None
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
            source=["ocr.tesseract.consensus"],
            frame_refs=[reading.frame_index],
            needs_review=True,
            period=self._period,
            match_clock_ms=reading.match_clock_ms,
            score_transition=transition,
            original_model_output={
                "raw_text": reading.raw_text,
                "raw_confidence": reading.raw_confidence,
            },
        )
        return self._public(reading), event

    @staticmethod
    def _public(reading: ParsedScoreboard) -> ScoreboardObservation:
        return ScoreboardObservation.model_validate(
            reading.model_dump(exclude={"raw_text", "raw_confidence"})
        )
