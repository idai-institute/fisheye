import pytest
from pydantic import ValidationError
from fisheye.schema.events import EventEnvelope


def test_legacy_group_is_preserved_without_fabricated_relationships():
    e = EventEnvelope(event_type="agent.start", agent_id="a", run_id="old")
    assert e.schema_version == "1" and e.workflow == "old"
    assert e.agent_instance_id is None and e.links == []


def test_versioned_relationships_validate_payload_and_isolate_scope():
    data = dict(schema_version="2", event_type="task.delegated", agent_id="a", run_id="r",
                workflow_id="w", payload={"task_id":"t", "recipient_id":"b"})
    a = EventEnvelope(**data)
    b = EventEnvelope(**data, application_id="another")
    assert a.scope != b.scope
    with pytest.raises(ValidationError):
        EventEnvelope(**dict(data, payload={"recipient_id":"b"}))
    with pytest.raises(ValidationError):
        EventEnvelope(**dict(data, payload={"task_id":"t", "recipient_id":"b", "typo":True}))


def test_event_size_and_nonfinite_values_rejected():
    for payload in [{"x":float("nan")}, {"x":"x" * 262144}]:
        with pytest.raises(ValidationError):
            EventEnvelope(event_type="custom.test", agent_id="a", run_id="r", payload=payload)
