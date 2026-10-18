"""Compare a fixed representative workload on the actual standard CI runner."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    "tests/test_srd_lite_foundation.py::test_levelled_spells_spend_one_actual_combat_action_and_slot",
    "tests/test_srd_lite_healing.py::test_admitted_healing_cast_actual_dice_budget_card_retry_reopen",
    "tests/test_srd_lite_healing.py::test_dead_healing_target_rejects_without_spend",
    "tests/test_runtime_procedure_failures.py",
    "tests/test_fc_campaign_progress.py::test_normal_two_act_campaign_completes_and_preserves_the_players_choice[finish]",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    results = []
    for workers, scheduler in [(1, "module"), (2, "module"), (2, "case")]:
        name = f"workers-{workers}-{scheduler}"
        selection = {
            "schema": 1,
            "mode": "affected",
            "source": source,
            "base": source,
            "python": {"representative-workload": CASES},
            "ui": [],
            "native": False,
            "documents": [],
            "reasons": ["Fixed workload comparison; not release acceptance"],
        }
        manifest = output / f"{name}-selection.json"
        manifest.write_text(json.dumps(selection, indent=2), encoding="utf-8")
        started = time.monotonic()
        process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/check_supported_journeys.py"),
                "--selection-manifest",
                str(manifest),
                "--jobs",
                str(workers),
                "--case-workers",
                "1",
                "--scheduler",
                scheduler,
                "--output-dir",
                str(output / name),
            ],
            cwd=ROOT,
            check=False,
        )
        row = {
            "workers": workers,
            "scheduler": scheduler,
            "elapsed_seconds": time.monotonic() - started,
            "exit_code": process.returncode,
        }
        results.append(row)
        (output / "performance.json").write_text(
            json.dumps(
                {
                    "source": source,
                    "cases": CASES,
                    "logical_cpus": os.cpu_count(),
                    "results": results,
                    "qualification": "representative comparison only; not full acceptance",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print("BENCHMARK " + json.dumps(row), flush=True)
    return int(any(row["exit_code"] for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
