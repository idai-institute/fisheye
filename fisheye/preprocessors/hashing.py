from __future__ import annotations

import hashlib
from collections.abc import Iterable

from fisheye.preprocessors.base import Preprocessor
from fisheye.schema.events import EventEnvelope


class HashFingerprintPreprocessor(Preprocessor):
    def __init__(self, fields_to_hash: Iterable[str] | None = None) -> None:
        self.fields_to_hash = tuple(fields_to_hash or ("content", "text", "message", "input", "output"))

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    async def process(self, event: EventEnvelope) -> list[EventEnvelope]:
        hashes: dict[str, str] = {}
        for field in self.fields_to_hash:
            raw = event.payload.get(field)
            if isinstance(raw, str) and raw:
                hashes[field] = self._digest(raw)

        if hashes:
            event.meta.setdefault("hashes", {}).update(hashes)
        return [event]
