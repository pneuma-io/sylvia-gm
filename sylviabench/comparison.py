"""Compare compatible batches while retaining every original manifest and result identity."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .contracts import ROOT, Result, digest, read_json, write_json
from .report import load_results, render


def comparison(directories: list[Path], output: Path) -> Path:
    """Require matched execution bytes, fixture pack, scenarios and attempt policy."""
    if len(directories) < 2:
        raise ValueError("Compare at least two explicitly selected batches")
    inputs = [p.resolve() for p in directories]
    output = output.resolve()
    if len(set(inputs)) != len(inputs):
        raise ValueError("Duplicate comparison input")
    if (
        output == ROOT
        or ROOT.is_relative_to(output)
        or any(p == output or p.is_relative_to(output) for p in inputs)
    ):
        raise ValueError("Comparison output must be a separate dedicated directory")
    batches = [load_results(p) for p in inputs]

    def policy(manifest: dict[str, Any]) -> dict[str, Any]:
        if not manifest.get("fixture_pack_sha256"):
            raise ValueError("Cross-batch comparison requires an explicit frozen fixture pack")
        return {
            "source_bytes": {k: v for k, v in manifest["source"].items() if k != "commit"},
            **{
                k: manifest.get(k)
                for k in (
                    "scenarios",
                    "repetitions",
                    "retries",
                    "randomness_policy",
                    "provider_requests_per_run",
                    "fixture_pack_sha256",
                )
            },
            "owner_reply_timeout_seconds": manifest.get("owner_reply_timeout_seconds", 300),
        }

    common = policy(batches[0][0])
    if any(policy(manifest) != common for manifest, _rows in batches[1:]):
        raise ValueError("Comparison policy mismatch: source, fixture, scenarios or attempt policy")
    seeds = [read_json(p / "seed.json") for p in inputs]
    if len({seed["database_sha256"] for seed in seeds}) != 1:
        raise ValueError("Comparison starting database mismatch")
    selected: dict[str, Any] = {}
    rows: list[Result] = []
    for manifest, records in batches:
        for setup in manifest["setups"]:
            if setup["id"] in selected:
                raise ValueError("Repeated setup ID across batches; use distinct setup identities")
            selected[setup["id"]] = setup
        rows.extend(records)
    descriptor = {
        "schema_version": 1,
        "kind": "comparison",
        "policy": common,
        "inputs": [
            {
                "path": os.path.relpath(p, output),
                "suite_id": manifest["suite_id"],
                "source": manifest["source"],
            }
            for p, (manifest, _) in zip(inputs, batches, strict=True)
        ],
    }
    descriptor["comparison_id"] = digest(descriptor)
    record = output / "comparison.json"
    if output.exists() and (not record.exists() or read_json(record) != descriptor):
        raise ValueError("Comparison output already belongs to another identity")
    output.mkdir(parents=True, exist_ok=True)
    write_json(record, descriptor)
    manifest = {
        "suite_id": descriptor["comparison_id"],
        "setups": list(selected.values()),
        "scenarios": batches[0][0]["scenarios"],
        "repetitions": batches[0][0]["repetitions"],
        "source": {"comparison_policy": common, "original_batches": descriptor["inputs"]},
    }
    return render(output, manifest, rows)
