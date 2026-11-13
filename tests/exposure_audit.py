"""Opt-in pytest plugin: prove SRD Lite exposure annotations never hide a tool a test really uses.

Every SRD Lite mechanic runs through ``materialize_package_mechanic``. This plugin wraps that
factory. When a mechanic returns without refusing, it checks that the same tool would have been
listed for the GM in the state the mechanic started from, and that a scoped casting tool's spell
was inside the tool's scope. Violations are written to the JSON report named by
``SYLVIA_GM_EXPOSURE_AUDIT`` and fail the session.

Usage:
    SYLVIA_GM_EXPOSURE_AUDIT=exposure-audit.json pytest -p tests.exposure_audit tests/test_srd_*.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.agent._tool_exposure import (
    EXPOSURE_KEY,
    SPELL_SCOPE_KEY,
    ExposureContext,
    exposure_spell_ids,
)

_RECORD = Path(__file__).resolve().parents[1] / "rulesets/srd-lite/package-record.json"
_used: Counter[str] = Counter()
_violations: list[dict[str, Any]] = []


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


_Scopes = dict[str, tuple[set[str], dict[str, str]]]


def _annotations() -> tuple[dict[str, Mapping[str, Any]], _Scopes]:
    record = json.loads(_RECORD.read_text(encoding="utf-8"))
    exposure: dict[str, Mapping[str, Any]] = {}
    scopes: _Scopes = {}
    for tool in record["tools"]:
        schema = tool["input_schema"]
        if EXPOSURE_KEY in schema:
            exposure[tool["name"]] = schema
        spell = schema["properties"].get("spell")
        if isinstance(spell, Mapping) and SPELL_SCOPE_KEY in spell:
            scopes[tool["name"]] = (
                set(spell[SPELL_SCOPE_KEY]),
                dict(spell.get("x-sylvia-gm-value-aliases", {})),
            )
    return exposure, scopes


def _castable(state: Mapping[str, Any], participants: Mapping[str, Any]) -> frozenset[str]:
    """Mirror ``_stateless_mcp._castable_spell_ids`` for a raw mechanic context."""

    found: set[str] = set()
    for participant in participants.values():
        data = participant.get("data", {}) if isinstance(participant, Mapping) else {}
        book = data.get("wizard_spellbook", ()) if isinstance(data, Mapping) else ()
        if isinstance(book, list):
            found.update(value for value in book if isinstance(value, str))
    for key, entities in state.items():
        if key.endswith(".entities") and isinstance(entities, Mapping):
            for entity in entities.values():
                prepared = entity.get("prepared_spells", ()) if isinstance(entity, Mapping) else ()
                if isinstance(prepared, list):
                    found.update(value for value in prepared if isinstance(value, str))
    return frozenset(found)


def _append(row: dict[str, Any]) -> None:
    """Write each audited use as it happens, so an interrupted run still reports."""
    target = os.environ.get("SYLVIA_GM_EXPOSURE_AUDIT")
    if target:
        with open(f"{target}.jsonl", "a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")


def pytest_configure(config: pytest.Config) -> None:
    from sylvia_gm.application import package_runtime

    exposure, scopes = _annotations()
    asked = exposure_spell_ids(exposure.values())
    original = package_runtime.materialize_package_mechanic

    def audited_factory(**kwargs: Any) -> Any:
        mechanic = original(**kwargs)
        name = kwargs["declaration"].name
        if name not in exposure and name not in scopes:
            return mechanic

        def audited(context: Any) -> Any:
            state = _plain(context.state)
            participants = _plain(context.participants)
            parameters = _plain(context.parameters)
            outcome = mechanic(context)
            _used[name] += 1
            node = os.environ.get("PYTEST_CURRENT_TEST", "")
            checked = len(_violations)
            if name in exposure:
                shown = ExposureContext(
                    state,
                    castable_spell_ids=_castable(state, participants),
                    asked_spell_ids=asked,
                ).exposed(exposure[name])
                if not shown:
                    _violations.append({"tool": name, "test": node, "kind": "hidden"})
            if name in scopes:
                allowed, aliases = scopes[name]
                spell = parameters.get("spell")
                spell = aliases.get(spell, spell)
                if spell not in allowed:
                    _violations.append(
                        {"tool": name, "test": node, "kind": "out_of_scope", "spell": spell}
                    )
            _append({"tool": name, "test": node, "ok": len(_violations) == checked})
            return outcome

        return audited

    package_runtime.materialize_package_mechanic = audited_factory
    # Rebind names that modules imported before this plugin configured.
    for module in list(sys.modules.values()):
        if getattr(module, "materialize_package_mechanic", None) is original:
            module.materialize_package_mechanic = audited_factory


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    target = os.environ.get("SYLVIA_GM_EXPOSURE_AUDIT")
    report = {"used": dict(sorted(_used.items())), "violations": _violations}
    if target:
        Path(target).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if _violations:
        session.exitstatus = 1
