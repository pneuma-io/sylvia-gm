"""JSON checkpoints for bounded consumer replay, including pending choices and receipts."""

from dataclasses import fields, is_dataclass
from enum import Enum
import json
from pathlib import Path
import random

from . import model, rng
from .engine import EventEngine, EventLogEntry
from .parameters import default_manifest

TYPES = {
    value.__name__: value
    for module in (model, rng)
    for value in vars(module).values()
    if isinstance(value, type) and (is_dataclass(value) or issubclass(value, Enum))
}
TYPES[EventLogEntry.__name__] = EventLogEntry


def encode(value):
    if isinstance(value, Enum):
        return {"type": type(value).__name__, "value": value.value}
    if isinstance(value, random.Random):
        return {"type": "Random", "value": encode(value.getstate())}
    if is_dataclass(value):
        return {
            "type": type(value).__name__,
            "value": {
                f.name: encode(getattr(value, f.name))
                for f in fields(value)
                if f.name != "_zone_digest"
            },
        }
    if isinstance(value, (set, tuple)):
        return {"type": type(value).__name__, "value": [encode(v) for v in value]}
    if isinstance(value, dict):
        return {"type": "dict", "value": [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    return value


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    kind, data = value["type"], value["value"]
    if kind == "dict":
        return {decode(k): decode(v) for k, v in data}
    if kind in {"set", "tuple"}:
        return (set if kind == "set" else tuple)(decode(v) for v in data)
    if kind == "Random":
        result = random.Random()
        result.setstate(decode(data))
        return result
    cls = TYPES[kind]
    if issubclass(cls, Enum):
        return cls(data)
    result = cls(**{f.name: decode(data[f.name]) for f in fields(cls) if f.init})
    for f in fields(cls):
        if not f.init and f.name in data:
            object.__setattr__(result, f.name, decode(data[f.name]))
    return result


def save(path: Path, engine: EventEngine, metrics):
    payload = {
        "format": 1,
        "parameters": engine.parameters.values,
        "parameter_hash": engine.parameters.hash,
        "seed": engine.master_seed,
        "capture_log": engine.capture_log,
        "state": encode(engine.state),
        "rng": encode(engine.rng),
        "log": encode(engine.log),
        "event_count": engine.event_count,
        "pending": engine.pending,
        "receipts": encode(engine.receipts),
        "metrics": metrics,
    }
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def reopen(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["format"] != 1:
        raise ValueError("unsupported lab checkpoint format")
    parameters = default_manifest().load_set(payload["parameters"])
    if parameters.hash != payload["parameter_hash"]:
        raise ValueError("checkpoint parameter identity mismatch")
    engine = EventEngine(
        decode(payload["state"]), parameters, payload["seed"], payload["capture_log"]
    )
    engine.rng, engine.log = decode(payload["rng"]), decode(payload["log"])
    engine.event_count, engine.pending = payload["event_count"], payload["pending"]
    engine.receipts = decode(payload["receipts"])
    return engine, payload["metrics"]
