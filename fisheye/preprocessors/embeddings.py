from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from fisheye.preprocessors.base import Preprocessor
from fisheye.preprocessors.utils import walk_strings
from fisheye.schema.events import EventEnvelope


class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class LocalHashEmbeddingProvider:
    """Deterministic, dependency-free embedding backend for local operation."""

    def __init__(self, dimensions: int = 64) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be > 0")
        self.dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0:
            return vector
        return [value / norm for value in vector]


class NoopEmbeddingProvider:
    async def embed(self, text: str) -> list[float]:
        return []


class EmbeddingPreprocessor(Preprocessor):
    def __init__(
        self,
        provider: EmbeddingProvider | None = None,
        max_chars: int = 2048,
    ) -> None:
        self.provider = provider or LocalHashEmbeddingProvider()
        self.max_chars = max_chars

    async def process(self, event: EventEnvelope) -> list[EventEnvelope]:
        text = "\n".join(walk_strings(event.payload))[: self.max_chars]
        if not text:
            return [event]

        vector = await self.provider.embed(text)
        if vector:
            event.meta["embedding"] = vector
        return [event]
