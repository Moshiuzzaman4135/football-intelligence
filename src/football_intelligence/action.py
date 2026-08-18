"""Temporal action-spotting interface and normalized evidence.

`ActionSpotter` is the replaceable interface the pipeline uses for broad
SoccerNet-v2 action spotting (goal / foul / card / corner / offside / ...). A
normalized `ActionSpot` carries the event type, milliseconds, confidence, and
the raw producer output so downstream fusion can combine independent evidence
(an action model + a stable OCR score change) without exposing any single
producer's internals.

The CALF adapter consumes precomputed SoccerNet features (one 512-d vector per
feature frame, 2 fps => 500 ms per frame). Extracting those features from a raw
video requires the legacy SoccerNet/ResNet stack, so the adapter is deliberately
isolated: ``torch`` is imported lazily inside the method that runs inference, and
the adapter never becomes a hard dependency of the core package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from football_intelligence.domain import (
    EventEvidence,
    FootballEvent,
)

# CALF / SoccerNet-v2 17-action taxonomy.
CALF_EVENTS: dict[str, int] = {
    "Penalty": 0,
    "Kick-off": 1,
    "Goal": 2,
    "Substitution": 3,
    "Offside": 4,
    "Shots on target": 5,
    "Shots off target": 6,
    "Clearance": 7,
    "Ball out of play": 8,
    "Throw-in": 9,
    "Foul": 10,
    "Indirect free-kick": 11,
    "Direct free-kick": 12,
    "Corner": 13,
    "Yellow card": 14,
    "Red card": 15,
    "Yellow->red card": 16,
}
CALF_LABELS: dict[int, str] = {index: label for label, index in CALF_EVENTS.items()}

# Normalized internal event types. Unknown/unsupported raw labels map to None so
# they are safely ignored rather than polluting the semantic timeline.
_LABEL_TO_EVENT = {
    "Goal": "goal_candidate",
    "Penalty": "penalty_candidate",
    "Foul": "foul_candidate",
    "Corner": "corner_candidate",
    "Yellow card": "yellow_card_candidate",
    "Red card": "red_card_candidate",
    "Yellow->red card": "red_card_candidate",
    "Offside": "offside_candidate",
    "Substitution": "substitution_candidate",
    "Shots on target": "shot_on_target_candidate",
    "Shots off target": "shot_off_target_candidate",
    "Direct free-kick": "direct_free_kick_candidate",
    "Indirect free-kick": "indirect_free_kick_candidate",
    "Throw-in": "throw_in_candidate",
    "Kick-off": "kick_off_candidate",
    "Clearance": "clearance_candidate",
    "Ball out of play": "ball_out_candidate",
}


@dataclass(frozen=True)
class ActionSpot:
    """A single normalized action prediction from any producer."""

    event_type: str
    start_ms: int
    end_ms: int
    confidence: float
    raw_label: str
    raw_score: float
    producer: str
    producer_version: str
    frame_index: int | None = None
    detail: str | None = None


class ActionSpotter(Protocol):
    """Replaceable broad action-spotting interface."""

    def spot(self, video_or_chunk: object) -> list[ActionSpot]: ...


def normalize_label(raw_label: str) -> str | None:
    """Map a producer's raw label to an internal event type, or ``None``.

    Unknown labels are safely ignored instead of being surfaced as semantic
    events. This keeps the default timeline meaningful when a producer emits
    classes the project does not yet present.
    """
    return _LABEL_TO_EVENT.get(raw_label)


def normalize_action(
    spot: ActionSpot,
    *,
    job_id: str,
    match_clock_ms: int | None = None,
    period: int | None = None,
) -> FootballEvent:
    """Translate a normalized ``ActionSpot`` into the existing ``FootballEvent``.

    Raises ``ValueError`` if the spot's event type is not a known normalized
    type, mirroring the safe-ignore policy for unknown raw labels.
    """
    if normalize_label(spot.raw_label) is None and spot.event_type not in _LABEL_TO_EVENT.values():
        raise ValueError(f"cannot normalize unknown action label {spot.raw_label!r}")
    description = f"{spot.raw_label} detected"
    evidence = EventEvidence(
        kind="action_spot",
        value=spot.raw_label,
        confidence=spot.confidence,
        frame_refs=[spot.frame_index] if spot.frame_index is not None else [],
        detail=spot.detail or f"raw score {spot.raw_score:.3f}",
    )
    return FootballEvent(
        job_id=job_id,
        event_type=spot.event_type,
        start_ms=spot.start_ms,
        end_ms=spot.end_ms,
        game_time=(
            f"{match_clock_ms // 60_000:02d}:{match_clock_ms // 1_000 % 60:02d}"
            if match_clock_ms is not None
            else None
        ),
        description=description,
        confidence=spot.confidence,
        evidence=[evidence],
        source=[spot.producer],
        frame_refs=[spot.frame_index] if spot.frame_index is not None else [],
        needs_review=True,
        period=period,
        match_clock_ms=match_clock_ms,
        producer_version=spot.producer_version,
        original_model_output={
            "raw_label": spot.raw_label,
            "raw_score": spot.raw_score,
            "producer": spot.producer,
            "producer_version": spot.producer_version,
        },
    )


def action_spots_from_features(
    spotting: np.ndarray,
    *,
    feature_length: int,
    frame_ms: int = 500,
    nms_delta_ms: int = 20_000,
    producer: str = "action.calf",
    producer_version: str = "unknown",
) -> list[ActionSpot]:
    """Decode a CALF spotting tensor ``(n_detections, 2 + n_classes)`` to spots.

    Each detection is ``[object_conf, normalized_frame_pos, ...class_scores]``.
    The normalized frame position maps to an absolute feature-frame index via
    ``floor(frame_pos * (feature_length - 1))`` (the same mapping CALF uses to
    place a detection inside a chunk); time is ``frame_index * frame_ms``. A
    per-class NMS over ``nms_delta_ms`` keeps the strongest detection in each
    class/time neighborhood, matching CALF evaluation. Detections whose class is
    not in the 17-action taxonomy are skipped; unknown labels are normalized to
    ``None`` and dropped here so they never reach the semantic timeline.
    """
    if spotting.ndim != 2 or spotting.shape[1] < 3:
        raise ValueError("spotting tensor must have shape (n_detections, 2+n_classes)")
    if feature_length < 1:
        raise ValueError("feature_length must be positive")
    n_detections, width = spotting.shape
    n_classes = width - 2
    nms_window_frames = max(1, round(nms_delta_ms / frame_ms))
    # Per-class strongest confidence per feature frame, after placing detections.
    class_confidence: list[list[tuple[int, float, int]]] = [
        [] for _ in range(n_classes)
    ]  # (feature_index, conf, argmax_score) for the top candidate
    for i in range(n_detections):
        conf = float(spotting[i, 0])
        if conf <= 0:
            continue
        frame_pos = float(spotting[i, 1])
        class_scores = spotting[i, 2:]
        class_index = int(np.argmax(class_scores))
        if class_index >= n_classes:
            continue
        feature_index = min(
            feature_length - 1, int(np.floor(frame_pos * (feature_length - 1)))
        )
        class_confidence[class_index].append((feature_index, conf, i))
    spots: list[ActionSpot] = []
    for class_index, candidates in enumerate(class_confidence):
        if class_index >= len(CALF_LABELS):
            continue
        raw_label = CALF_LABELS[class_index]
        event_type = normalize_label(raw_label)
        if event_type is None:
            continue
        if not candidates:
            continue
        # Sort by confidence descending; apply NMS over the delta window.
        candidates.sort(key=lambda item: item[1], reverse=True)
        kept: list[tuple[int, float, int]] = []
        for feature_index, conf, argmax_score in candidates:
            if any(
                abs(feature_index - other[0]) <= nms_window_frames for other in kept
            ):
                continue
            kept.append((feature_index, conf, argmax_score))
        for feature_index, conf, _argmax_score in sorted(kept):
            start_ms = feature_index * frame_ms
            spots.append(
                ActionSpot(
                    event_type=event_type,
                    start_ms=start_ms,
                    end_ms=start_ms + frame_ms,
                    confidence=conf,
                    raw_label=raw_label,
                    raw_score=float(conf),
                    producer=producer,
                    producer_version=producer_version,
                    frame_index=feature_index,
                )
            )
    return sorted(spots, key=lambda item: item.start_ms)


class CalfActionSpotter:
    """CALF 17-action spotter over precomputed SoccerNet features.

    ``torch`` is imported lazily inside ``spot`` so the core package never
    depends on it. The adapter consumes a features array of shape
    ``(feature_frames, 512)`` at 2 fps; callers must provide features (e.g. via
    the isolated SoccerNet ResNet/PCA stack) because raw-video feature extraction
    is deliberately not a core dependency.
    """

    def __init__(
        self,
        weights_path: str,
        *,
        producer: str = "action.calf",
        producer_version: str = "calf",
        num_features: int = 512,
        num_classes: int = 17,
        num_detections: int = 15,
        chunk_seconds: int = 120,
        receptive_field_seconds: int = 40,
        frame_ms: int = 500,
        threshold: float = 0.0,
        device: str | None = None,
    ) -> None:
        self._weights_path = weights_path
        self._producer = producer
        self._producer_version = producer_version
        self._num_features = num_features
        self._num_classes = num_classes
        self._num_detections = num_detections
        self._chunk_frames = chunk_seconds * 2
        self._receptive_frames = receptive_field_seconds * 2
        self._frame_ms = frame_ms
        self._threshold = threshold
        self._device = device
        self._model = None
        self._feature_index: int | None = None
        self._feature_length: int | None = None

    def _load(self) -> object:
        if self._model is not None:
            return self._model
        import torch  # isolated, lazy import

        from football_intelligence.action_calf_model import ContextAwareModel

        device = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
        checkpoint = torch.load(self._weights_path, map_location=device)
        model = ContextAwareModel(
            input_size=self._num_features,
            num_classes=self._num_classes,
            chunk_size=self._chunk_frames,
            receptive_field=self._receptive_frames,
            num_detections=self._num_detections,
        )
        model.load_state_dict(checkpoint["state_dict"], strict=True)
        model.to(device).eval()
        self._model = model
        self._feature_index = 0
        return model

    def spot(self, video_or_chunk: object) -> list[ActionSpot]:
        features = np.asarray(video_or_chunk, dtype=np.float32)
        if features.ndim != 2 or features.shape[1] != self._num_features:
            raise ValueError(
                f"features must be shape (frames, {self._num_features})"
            )
        model = self._load()
        import torch

        device = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
        feature_length = features.shape[0]
        n_classes = self._num_classes
        # Accumulate per-class, per-feature-frame confidence (like timestamps2long).
        acc = np.full((feature_length, n_classes), -1.0, dtype=np.float64)
        start = 0
        rf = self._receptive_frames // 2
        chunk = self._chunk_frames
        last = False
        while True:
            if start == 0:
                write_begin, write_end = 0, chunk - rf
            elif last:
                write_begin, write_end = start + rf, start + chunk
            else:
                write_begin, write_end = start + rf, start + chunk - rf
            tensor = torch.from_numpy(
                features[start : start + chunk]
            ).unsqueeze(0).unsqueeze(0).to(device)
            with torch.no_grad():
                _seg, spotting = model(tensor)
            spotting_np = spotting.cpu().numpy()[0]  # (num_detections, 2+n_classes)
            chunk_acc = np.full((chunk, n_classes), -1.0, dtype=np.float64)
            for detection in spotting_np:
                conf = float(detection[0])
                if conf <= 0:
                    continue
                frame_pos = float(detection[1])
                class_scores = detection[2:]
                class_index = int(np.argmax(class_scores))
                if class_index >= n_classes:
                    continue
                f_index = min(chunk - 1, int(np.floor(frame_pos * (chunk - 1))))
                chunk_acc[f_index, class_index] = max(
                    chunk_acc[f_index, class_index], conf
                )
            lo = max(0, min(write_begin, feature_length))
            hi = min(write_end, feature_length)
            if hi > lo:
                acc[lo:hi] = chunk_acc[lo - start : hi - start]
            if last:
                break
            start += chunk - 2 * rf
            if start + chunk >= feature_length:
                start = feature_length - chunk
                last = True
        spots: list[ActionSpot] = []
        for class_index in range(n_classes):
            if class_index >= len(CALF_LABELS):
                continue
            raw_label = CALF_LABELS[class_index]
            event_type = normalize_label(raw_label)
            if event_type is None:
                continue
            frame_indices = np.where(acc[:, class_index] > self._threshold)[0]
            for feature_index in frame_indices:
                conf = float(acc[feature_index, class_index])
                start_ms = int(feature_index) * self._frame_ms
                spots.append(
                    ActionSpot(
                        event_type=event_type,
                        start_ms=start_ms,
                        end_ms=start_ms + self._frame_ms,
                        confidence=conf,
                        raw_label=raw_label,
                        raw_score=conf,
                        producer=self._producer,
                        producer_version=self._producer_version,
                        frame_index=int(feature_index),
                    )
                )
        return _nms_spots(spots, delta_ms=self._frame_ms * 20)


def _nms_spots(spots: list[ActionSpot], *, delta_ms: int) -> list[ActionSpot]:
    """Per-event-type NMS: keep the strongest spot in each time neighborhood."""
    kept: list[ActionSpot] = []
    for event_type in sorted({spot.event_type for spot in spots}):
        typed = [spot for spot in spots if spot.event_type == event_type]
        typed.sort(key=lambda item: item.start_ms)
        result: list[ActionSpot] = []
        for spot in typed:
            if any(
                abs(spot.start_ms - other.start_ms) <= delta_ms for other in result
            ):
                if spot.confidence > result[-1].confidence:
                    result[-1] = spot
                continue
            result.append(spot)
        kept.extend(result)
    return sorted(kept, key=lambda item: item.start_ms)
