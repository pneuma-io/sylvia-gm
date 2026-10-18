"""Optional setup facts from Sylvia's own Codex catalog, never account credentials."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sylvia_gm.provider._model_defaults import documented_model_defaults


def subscription_model_facts(home: Path, model_ids: set[str]) -> dict[str, dict[str, object]]:
    """Supplement advertised identities without treating API context as subscription capacity.

    model/list omits token capacities. The runtime's recent local catalog supplies its default
    context and effective percentage; the cache is optional and an unfamiliar shape fails closed.
    The documented output ceiling is only a model reference: Codex controls every actual reply.
    """
    facts: dict[str, dict[str, object]] = {}
    for model_id in model_ids:
        documented = documented_model_defaults("openai", model_id)
        if documented.get("max_output_tokens"):
            facts[model_id] = {
                "output_reference_tokens": documented["max_output_tokens"],
                "output_reference_source": documented["documented_source"],
            }
    try:
        path = home / "models_cache.json"
        if path.is_symlink():
            return facts
        with path.open("rb") as source:
            raw = source.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return facts
        catalog = json.loads(raw)
        if not isinstance(catalog, dict) or not isinstance(catalog.get("client_version"), str):
            return facts
        fetched = datetime.fromisoformat(catalog["fetched_at"])
        age = datetime.now(UTC) - fetched
        if not -timedelta(minutes=5) <= age <= timedelta(days=1):
            return facts
        rows = catalog.get("models")
        if not isinstance(rows, list):
            return facts
        for row in rows:
            if not isinstance(row, dict) or row.get("slug") not in model_ids:
                continue
            window, percent = row.get("context_window"), row.get("effective_context_window_percent")
            if (type(window) is not int or not 1 <= window <= 10_000_000
                    or type(percent) is not int or not 1 <= percent <= 100):
                continue
            allowance = window * percent // 100
            if allowance:
                facts.setdefault(row["slug"], {}).update(
                    runtime_context_window=allowance,
                    runtime_context_source="model_catalog",
                    runtime_context_observed_at=fetched.isoformat(),
                )
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        pass
    return facts
