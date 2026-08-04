import asyncio
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel

from fisheye.collectors.base import Collector
from fisheye.privacy import redact
from fisheye.runtime import build_default_runtime
from fisheye.schema.serialization import json_safe
from fisheye.tests.test_privacy_and_auth import config


def test_cyclic_and_shared_objects_are_redacted_without_string_callbacks():
    @dataclass
    class Node:
        password: str
        child: object = None

    class SecretKey:
        def __str__(self):
            raise AssertionError("Key string conversion leaks private data")

    node = Node("private")
    node.child = node
    assert redact(node) == {"password": "[REDACTED]", "child": "[CIRCULAR]"}
    cycle = {"api_key": "secret"}
    cycle["self"] = cycle
    assert redact(cycle) == {"api_key": "[REDACTED]", "self": "[CIRCULAR]"}
    assert redact({SecretKey(): node})["[key:SecretKey:0]"]["password"] == "[REDACTED]"
    shared = {"x": 1}
    assert json_safe([shared, shared]) == [{"x": 1}, {"x": 1}]


def test_model_cycles_enum_values_and_depth_are_normalized():
    class Model(BaseModel):
        child: object = None

    class Status(Enum):
        READY = "ready"

    model = Model()
    model.child = model
    assert json_safe(model) == {"child": "[CIRCULAR]"}
    assert json_safe(Status.READY) == "ready"
    root = cursor = []
    for _ in range(20):
        child = []
        cursor.append(child)
        cursor = child
    assert "[MAX_DEPTH]" in str(json_safe(root))


def test_event_extensions_are_redacted_before_optional_consumer_delivery(tmp_path):
    class Recorder(Collector):
        def __init__(self):
            self.events = []

        async def handle_event(self, event):
            self.events.append(event.model_dump(mode="json"))

    async def run():
        collector = Recorder()
        async with build_default_runtime(config(tmp_path)) as runtime:
            runtime.register_collector(collector)
            await runtime.publish(
                dict(
                    event_type="agent.start",
                    agent_id="a",
                    run_id="r",
                    password="extension-secret",
                    extra={"api_key": "nested-secret"},
                )
            )
            await runtime.drain(3)
            assert collector.events[0]["password"] == "[REDACTED]"
            assert collector.events[0]["extra"]["api_key"] == "[REDACTED]"
            assert "extension-secret" not in str(await runtime.store.journal_events())

    asyncio.run(run())
