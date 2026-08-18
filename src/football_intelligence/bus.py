"""Small synchronous semantic event bus for the single-process prototype."""

import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

LOGGER = logging.getLogger(__name__)


class EventEnvelope(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    topic: str
    payload: dict[str, Any]
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


Subscriber = Callable[[EventEnvelope], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)

    def subscribe(self, topic: str, subscriber: Subscriber) -> None:
        self._subscribers[topic].append(subscriber)

    def publish(self, topic: str, payload: dict[str, Any]) -> EventEnvelope:
        envelope = EventEnvelope(topic=topic, payload=payload)
        for subscriber in self._subscribers[topic]:
            try:
                subscriber(envelope)
            except Exception:
                LOGGER.exception("event subscriber failed", extra={"event_id": envelope.id})
        return envelope
