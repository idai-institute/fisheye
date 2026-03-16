from collections import deque
from datetime import datetime
from dataclasses import fields, is_dataclass
from fisheye.behavior.online_stats import EwmaTracker, ToolDistributionTracker

_TYPES = {t.__name__: t for t in (EwmaTracker, ToolDistributionTracker)}


def encode(value):
    if isinstance(value, datetime):
        return {'$type':'datetime', 'value':value.isoformat()}
    if isinstance(value, deque):
        return {'$type':'deque', 'value':[encode(v) for v in value], 'maxlen':value.maxlen}
    if isinstance(value, tuple):
        return {'$type':'tuple', 'value':[encode(v) for v in value]}
    if isinstance(value, dict):
        return {'$type':'dict', 'value':[[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if is_dataclass(value):
        return {'$type':type(value).__name__, 'value':{f.name:encode(getattr(value, f.name)) for f in fields(value)}}
    return value


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    kind, data = value['$type'], value['value']
    if kind == 'datetime':
        return datetime.fromisoformat(data)
    if kind == 'deque':
        return deque((decode(v) for v in data), maxlen=value['maxlen'])
    if kind == 'tuple':
        return tuple(decode(v) for v in data)
    if kind == 'dict':
        return {decode(k):decode(v) for k, v in data}
    if kind in _TYPES:
        return _TYPES[kind](**{k:decode(v) for k, v in data.items()})
    raise ValueError(f'Unsupported checkpoint type: {kind}')
