from __future__ import annotations

import json

from fisheye.preprocessors.base import Preprocessor
from fisheye.preprocessors.utils import walk_strings
from fisheye.schema.events import EventEnvelope


class FeatureExtractionPreprocessor(Preprocessor):
    async def process(self, event: EventEnvelope) -> list[EventEnvelope]:
        texts = list(walk_strings(event.payload))
        total_chars = sum(len(text) for text in texts)
        total_words = sum(len(text.split()) for text in texts)
        max_len = max((len(text) for text in texts), default=0)
        approx_tokens = max(total_words, total_chars // 4)

        event.meta.setdefault("features", {}).update(
            {
                "string_count": len(texts),
                "total_chars": total_chars,
                "total_words": total_words,
                "max_string_length": max_len,
                "approx_tokens": approx_tokens,
            }
        )
        return [event]


class FeatureOnlyProjectionPreprocessor(Preprocessor):
    async def process(self, event: EventEnvelope) -> list[EventEnvelope]:
        original_size = len(json.dumps(event.payload, default=str))
        event.payload = {
            "event_type": event.event_type,
            "hashes": event.meta.get("hashes", {}),
            "features": event.meta.get("features", {}),
            "domains": event.meta.get("domains", []),
            "original_payload_bytes": original_size,
        }
        event.meta["feature_only"] = True
        return [event]
