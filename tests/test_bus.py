from football_intelligence.bus import EventBus


def test_subscriber_failure_does_not_hide_event_from_other_subscribers():
    bus = EventBus()
    received = []

    def broken(_envelope):
        raise RuntimeError("subscriber unavailable")

    bus.subscribe("job.started", broken)
    bus.subscribe("job.started", received.append)

    envelope = bus.publish("job.started", {"job_id": "job-1"})

    assert received == [envelope]
    assert envelope.topic == "job.started"
    assert envelope.payload == {"job_id": "job-1"}
    assert envelope.published_at.tzinfo is not None

