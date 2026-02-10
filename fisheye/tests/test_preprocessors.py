from __future__ import annotations

import asyncio

from fisheye.preprocessors.embeddings import EmbeddingPreprocessor
from fisheye.preprocessors.features import FeatureExtractionPreprocessor
from fisheye.preprocessors.hashing import HashFingerprintPreprocessor
from fisheye.preprocessors.redaction import PIIRedactionPreprocessor, SecretRedactionPreprocessor
from fisheye.preprocessors.urls import URLDomainExtractionPreprocessor
from fisheye.schema.events import EventEnvelope


def test_secret_and_pii_redaction() -> None:
    event = EventEnvelope(
        event_type="llm.request",
        agent_id="agent",
        run_id="run",
        payload={
            "message": "contact me at user@example.com and api_key=ABCDEF1234567890",
        },
    )

    async def _run() -> EventEnvelope:
        redacted = (await SecretRedactionPreprocessor().process(event))[0]
        redacted = (await PIIRedactionPreprocessor().process(redacted))[0]
        return redacted

    out = asyncio.run(_run())
    assert "user@example.com" not in out.payload["message"]
    assert "api_key=" not in out.payload["message"].lower()


def test_hash_and_feature_extraction() -> None:
    event = EventEnvelope(
        event_type="llm.request",
        agent_id="agent",
        run_id="run",
        payload={"message": "hello world"},
    )

    async def _run() -> EventEnvelope:
        event1 = (await HashFingerprintPreprocessor(fields_to_hash=["message"]).process(event))[0]
        return (await FeatureExtractionPreprocessor().process(event1))[0]

    out = asyncio.run(_run())
    assert out.meta["hashes"]["message"]
    assert out.meta["features"]["approx_tokens"] > 0


def test_url_domain_extraction() -> None:
    event = EventEnvelope(
        event_type="llm.message",
        agent_id="agent",
        run_id="run",
        payload={"text": "send this to https://example.com/path?q=1"},
    )

    async def _run() -> EventEnvelope:
        return (await URLDomainExtractionPreprocessor().process(event))[0]

    out = asyncio.run(_run())
    assert "example.com" in out.meta["domains"]


def test_embedding_preprocessor_local_backend() -> None:
    event = EventEnvelope(
        event_type="llm.request",
        agent_id="agent",
        run_id="run",
        payload={"message": "this is a real embedding signal"},
    )

    async def _run() -> EventEnvelope:
        return (await EmbeddingPreprocessor().process(event))[0]

    out = asyncio.run(_run())
    assert "embedding" in out.meta
    assert len(out.meta["embedding"]) == 64
