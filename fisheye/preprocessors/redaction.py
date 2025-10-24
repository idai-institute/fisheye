from __future__ import annotations

import re
from typing import Pattern

from fisheye.preprocessors.base import Preprocessor
from fisheye.preprocessors.utils import recursive_transform
from fisheye.schema.events import EventEnvelope


class _RegexRedactor(Preprocessor):
    patterns: tuple[Pattern[str], ...] = ()
    replacement: str = "[REDACTED]"

    def _redact_text(self, text: str) -> str:
        result = text
        for pattern in self.patterns:
            result = pattern.sub(self.replacement, result)
        return result

    async def process(self, event: EventEnvelope) -> list[EventEnvelope]:
        event.payload = recursive_transform(event.payload, self._redact_text)
        event.meta = recursive_transform(event.meta, self._redact_text)
        return [event]


class SecretRedactionPreprocessor(_RegexRedactor):
    patterns = (
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        re.compile(r"(?i)api[_-]?key\s*[:=]\s*[A-Za-z0-9_\-]{8,}"),
        re.compile(r"(?i)token\s*[:=]\s*[A-Za-z0-9_\-]{8,}"),
        re.compile(r"(?i)password\s*[:=]\s*\S+"),
    )


class PIIRedactionPreprocessor(_RegexRedactor):
    patterns = (
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        re.compile(r"\b(?:\+1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"),
    )
