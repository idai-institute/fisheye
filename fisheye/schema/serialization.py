from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


def json_safe(value, depth=0, _seen=None):
    if depth > 12:
        return "[MAX_DEPTH]"
    if isinstance(value, Enum):
        return json_safe(value.value, depth + 1, _seen)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (UUID, datetime)):
        return str(value)
    seen = set() if _seen is None else _seen
    if id(value) in seen:
        return "[CIRCULAR]"
    seen.add(id(value))
    try:
        if isinstance(value, BaseModel):
            # Read fields without calling custom serializers or recursing inside
            # model_dump before our cycle/depth checks can run.
            data = {name: getattr(value, name) for name in type(value).model_fields}
            data.update(value.model_extra or {})
            return json_safe(data, depth + 1, seen)
        if is_dataclass(value) and not isinstance(value, type):
            return json_safe({field.name: getattr(value, field.name) for field in fields(value)}, depth + 1, seen)
        if isinstance(value, dict):
            return {json_key(k, i): json_safe(v, depth + 1, seen) for i, (k, v) in enumerate(value.items())}
        if isinstance(value, (list, tuple)):
            return [json_safe(v, depth + 1, seen) for v in value]
        return {"object_type": type(value).__name__}
    finally:
        seen.remove(id(value))


def json_key(key, index=0):
    if key is None or isinstance(key, (str, int, float, bool, UUID)):
        return str(key)
    return f"[key:{type(key).__name__}:{index}]"
