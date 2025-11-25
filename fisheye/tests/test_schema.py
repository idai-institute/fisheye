from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from fisheye.schema.alerts import Alert
from fisheye.schema.events import EventEnvelope


def test_event_envelope_accepts_valid_payload() -> None:
    event = EventEnvelope(
        event_type="llm.request",
        agent_id="agent-1",
        run_id="run-1",
        payload={"message": "hello"},
    )

    assert event.event_id
    assert event.timestamp.tzinfo is not None


def test_event_envelope_rejects_invalid_event_type() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope(
            event_type="unknown.type",
            agent_id="agent-1",
            run_id="run-1",
            payload={},
        )


def test_alert_score_range_is_enforced() -> None:
    with pytest.raises(ValidationError):
        Alert(
            agent_id="a",
            run_id="r",
            category="dos",
            score=1.2,
            threshold=0.7,
            triggered=True,
        )


def test_event_timestamp_normalized_utc() -> None:
    event = EventEnvelope(
        event_type="llm.request",
        agent_id="agent",
        run_id="run",
        payload={},
        timestamp=datetime(2024, 1, 1, 10, 0, 0),
    )
    assert event.timestamp.utcoffset() is not None
