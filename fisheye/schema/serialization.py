from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


def json_safe(value, depth=0):
    if depth > 12:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (UUID, datetime, Enum)):
        return str(value)
    if hasattr(value, "model_dump"):
        return json_safe(value.model_dump(mode="json"), depth + 1)
    if is_dataclass(value):
        return json_safe(asdict(value), depth + 1)
    if isinstance(value, dict):
        return {str(k): json_safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v, depth + 1) for v in value]
    return {"object_type": type(value).__name__}
