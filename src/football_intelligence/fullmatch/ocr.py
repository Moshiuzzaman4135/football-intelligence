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

    def __init__(
        self,
        job_id: str,
        stable_score_ms: int = 5_000,
        *,
        producer: str = "ocr.tesseract",
        producer_version: str = "unknown",
        state: ConsensusState | None = None,
    ) -> None:
        self._job_id = job_id
        self._stable_score_ms = stable_score_ms
        self._producer = producer
        self._producer_version = producer_version
        self._period = 1
        self._last_clock_ms: int | None = None
        self._accepted_score: tuple[int, int] | None = None
        self._pending_score: tuple[int, int] | None = None
        self._pending_since_ms: int | None = None
        self._pending_last_ms: int | None = None
        self._pending_reads: list[ParsedScoreboard] = []
        if state is not None:
            self._period = state.period
            self._last_clock_ms = state.last_clock_ms
            self._accepted_score = state.accepted_score
            self._pending_score = state.pending_score
            self._pending_since_ms = state.pending_since_ms
            self._pending_last_ms = state.pending_last_ms
            self._pending_reads = list(state.pending_reads)

    def seed(self, observation: ScoreboardObservation) -> None:
        """Restore accepted state from a durable completed-chunk observation."""
        self._period = observation.period
        self._last_clock_ms = observation.match_clock_ms
        self._accepted_score = (observation.home_score, observation.away_score)
        self._pending_score = None
        self._pending_since_ms = None
        self._pending_last_ms = None
        self._pending_reads = []

    def snapshot(self) -> ConsensusState:
        return ConsensusState(
            period=self._period,
            last_clock_ms=self._last_clock_ms,
            accepted_score=self._accepted_score,
            pending_score=self._pending_score,
            pending_since_ms=self._pending_since_ms,
            pending_last_ms=self._pending_last_ms,
            pending_reads=self._pending_reads,
        )

    def observe_missing(self, timestamp_ms: int) -> None:
        del timestamp_ms
        self._clear_pending()

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
            self._clear_pending()
            return self._public(reading), None
        if score[0] < self._accepted_score[0] or score[1] < self._accepted_score[1]:
            return None, None
        if (
            score != self._pending_score
            or self._pending_last_ms is None
            or reading.timestamp_ms - self._pending_last_ms > 1_500
        ):
            self._pending_score = score
            self._pending_since_ms = reading.timestamp_ms
            self._pending_last_ms = reading.timestamp_ms
            self._pending_reads = [reading]
            return None, None
        self._pending_last_ms = reading.timestamp_ms
        self._pending_reads.append(reading)
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

    @staticmethod
    def _public(reading: ParsedScoreboard) -> ScoreboardObservation:
        return ScoreboardObservation.model_validate(
            reading.model_dump(exclude={"raw_text", "raw_confidence"})
        )


class ConsensusState(BaseModel):
    period: int = Field(default=1, ge=1)
    last_clock_ms: int | None = Field(default=None, ge=0)
    accepted_score: tuple[int, int] | None = None
    pending_score: tuple[int, int] | None = None
    pending_since_ms: int | None = Field(default=None, ge=0)
    pending_last_ms: int | None = Field(default=None, ge=0)
    pending_reads: list[ParsedScoreboard] = Field(default_factory=list)
