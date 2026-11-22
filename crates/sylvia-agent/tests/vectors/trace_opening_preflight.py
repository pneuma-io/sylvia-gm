"""Run the Python owner with the opening preflight traced, for `generate_opening_messages_vectors.py`.

Launched by sylvia-compare as the Python owner (`python -B trace_opening_preflight.py`). Wraps
the application owner and the bus given to `_preflight_guided_start` in a proxy that logs every
read with its arguments and full result, and logs the adapter's inputs and `_messages`' result.
Writes JSON lines to `opening_preflight_trace.jsonl` beside this file. Reference tooling only;
retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
TRACE_LOG = Path(__file__).with_name("opening_preflight_trace.jsonl")

import sylvia_gm.interfaces.owner_process as owner_process  # noqa: E402
from sylvia_gm.agent import _provider_session  # noqa: E402
from sylvia_gm.interfaces.commands import campaigns  # noqa: E402

DEPTH = {"value": 0}


def plain(value: object, depth: int = 0) -> object:
    """A JSON-safe copy: dataclasses as dicts, enums as values, else repr."""
    import dataclasses
    import enum
    from collections.abc import Mapping, Sequence
    if depth > 40:
        return "<deep>"
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"__class__": type(value).__name__,
                **{f.name: plain(getattr(value, f.name), depth + 1)
                   for f in dataclasses.fields(value)}}
    if isinstance(value, Mapping):
        return {str(k): plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, frozenset, set)):
        items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
        return [plain(item, depth + 1) for item in items]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes_hex__": bytes(value).hex()}
    if callable(getattr(value, "items", None)):
        return {"__class__": type(value).__name__,
                **{str(k): plain(v, depth + 1) for k, v in value.items()}}
    if hasattr(value, "__iter__"):
        return [plain(item, depth + 1) for item in value]
    return {"__repr__": repr(value), "__class__": type(value).__name__}


def log(entry: dict[str, object]) -> None:
    with TRACE_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, default=str) + "\n")


class Traced:
    def __init__(self, target: object, prefix: str = "") -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_prefix", prefix)

    def __getattr__(self, name: str) -> object:
        target = object.__getattribute__(self, "_target")
        label = object.__getattribute__(self, "_prefix") + name
        # Full Audit is forced on for the trace, and its records are captured, not stored.
        if label == "admin_full_audit_enabled":
            def enabled() -> bool:
                log({"event": "call", "name": label, "args": [], "kwargs": {}, "result": True})
                return True
            return enabled
        if label == "record_admin_full_audit":
            return lambda payload: log({"event": "audit", "record": plain(payload)})
        try:
            value = getattr(target, name)
        except AttributeError:
            log({"event": "missing", "name": label, "depth": DEPTH["value"]})
            raise
        if callable(value):
            def call(*args: object, **kwargs: object) -> object:
                try:
                    result = value(*args, **kwargs)
                except BaseException as error:
                    log({"event": "call", "name": label, "args": plain(list(args)),
                         "kwargs": plain(kwargs),
                         "raised": {"class": type(error).__name__, "message": str(error)}})
                    raise
                log({"event": "call", "name": label, "args": plain(list(args)),
                     "kwargs": plain(kwargs), "result": plain(result)})
                return result
            return call
        log({"event": "attribute", "name": label, "depth": DEPTH["value"]})
        return value

    def __hash__(self) -> int:
        return hash(object.__getattribute__(self, "_target"))

    def __eq__(self, other: object) -> bool:
        return object.__getattribute__(self, "_target") is other or self is other


original = campaigns._preflight_guided_start


def traced(*args: object, application_owner: object, bus: object, **kwargs: object) -> None:
    log({"event": "preflight_start",
         "kwargs": plain({k: v for k, v in kwargs.items() if k != "provider_resolver"})})
    resolver = kwargs["provider_resolver"]

    def traced_resolver(session: object) -> object:
        provider = resolver(session)
        log({"event": "resolved", "session": plain(session), "class": type(provider).__name__,
             "model": plain(getattr(provider, "model_info", None)),
             "base_url": getattr(provider, "_base_url", None)})
        return provider

    kwargs["provider_resolver"] = traced_resolver
    DEPTH["value"] += 1
    try:
        result = original(*args, application_owner=Traced(application_owner),
                          bus=Traced(bus, "bus."), **kwargs)
        log({"event": "preflight_ok"})
        return result
    except BaseException as error:
        log({"event": "preflight_error", "class": type(error).__name__, "message": str(error)[:300]})
        raise
    finally:
        DEPTH["value"] -= 1


campaigns._preflight_guided_start = traced
owner_process._preflight_guided_start = traced

original_ledger = _provider_session.ProviderSessionAdapter._ledger_active


def ledger_active(self: object, provider_input: object) -> bool:
    result = original_ledger(self, provider_input)
    log({"event": "ledger_active", "value": result,
         "context_mode": getattr(self, "_context_mode", None)})
    return result


_provider_session.ProviderSessionAdapter._ledger_active = ledger_active


original_init = _provider_session.ProviderSessionAdapter.__init__


def traced_init(self, owner, provider, **kwargs):
    if isinstance(owner, Traced):
        model = getattr(provider, "model_info", None)
        log({"event": "adapter", "kwargs": plain({k: v for k, v in kwargs.items()
                                                  if k != "record_warning"}),
             "model": plain(model), "provider_class": type(provider).__name__})
    return original_init(self, owner, provider, **kwargs)


_provider_session.ProviderSessionAdapter.__init__ = traced_init
original_messages = _provider_session.ProviderSessionAdapter._messages


def traced_messages(self, provider_input, **kwargs):
    result = original_messages(self, provider_input, **kwargs)
    if isinstance(self._owner, Traced):
        log({"event": "messages", "kwargs": plain(kwargs),
             "input": plain({"session": provider_input.session,
                             "history": provider_input.history,
                             "basis_message_id": provider_input.basis_message_id,
                             "recovery_kind": provider_input.recovery_kind,
                             "has_reference_reader": provider_input.reference_reader is not None}),
             "result": plain(result)})
    return result


_provider_session.ProviderSessionAdapter._messages = traced_messages

from sylvia_gm.provider import _context_fit  # noqa: E402

original_admit = _context_fit.ContextFittingProvider.admit_request


def traced_admit(self, messages, tools=None, **options):
    try:
        measured = original_admit(self, messages, tools, **options)
    except BaseException as error:
        log({"event": "admit", "options": plain(options),
             "raised": {"class": type(error).__name__, "message": str(error)}})
        raise
    log({"event": "admit", "options": plain(options), "measurement": measured.projection()})
    return measured


_context_fit.ContextFittingProvider.admit_request = traced_admit

if __name__ == "__main__":
    raise SystemExit(owner_process.main())
