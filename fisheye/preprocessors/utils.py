from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for nested in value.values():
            yield from walk_strings(nested)
        return
    if isinstance(value, list):
        for nested in value:
            yield from walk_strings(nested)


def recursive_transform(value: Any, transform: callable) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {k: recursive_transform(v, transform) for k, v in value.items()}
    if isinstance(value, list):
        return [recursive_transform(v, transform) for v in value]
    return value
