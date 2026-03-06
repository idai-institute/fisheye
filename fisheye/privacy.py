"""Content minimization shared by storage, export, and evidence sinks."""
from __future__ import annotations

import re
from typing import Any

from fisheye.preprocessors.redaction import PIIRedactionPreprocessor, SecretRedactionPreprocessor

_PATTERNS = (*SecretRedactionPreprocessor.patterns, *PIIRedactionPreprocessor.patterns,
             re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC )?PRIVATE KEY-----"))
_SENSITIVE_KEYS = re.compile(r"(?i)^(password|secret|api[_-]?key|access[_-]?token|authorization|cookie|private[_-]?key)$")


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pattern in _PATTERNS:
            value = pattern.sub("[REDACTED]", value)
        return value
    if isinstance(value, dict):
        return {redact(str(k)): "[REDACTED]" if _SENSITIVE_KEYS.match(str(k)) else redact(v)
                for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(v) for v in value]
    return value
