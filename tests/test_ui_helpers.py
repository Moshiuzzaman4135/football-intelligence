from football_intelligence.ui import confidence_label, format_evidence


def test_confidence_label_rounds_for_timeline_display():
    assert confidence_label(0.834) == "83%"


def test_format_evidence_keeps_kind_value_and_confidence():
    text = format_evidence(
        {"kind": "ball_speed_px_s", "value": 248.5, "confidence": 0.71}
    )

    assert text == "ball_speed_px_s: 248.5 (71%)"
