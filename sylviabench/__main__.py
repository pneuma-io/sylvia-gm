"""One-command developer interface. No command implicitly selects a real model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .checks import evaluate
from .comparison import comparison
from .contracts import scenarios, setups
from .packs import freeze, verify
from .report import load_results, report
from .runner import run, source_identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SylviaBench gameplay framework")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser(
        "setups", help="List explicit benchmark setups; never read credentials"
    )
    listing.add_argument("--config", type=Path)
    commands.add_parser("scenarios", help="List the versioned scenario set and checks")
    fixture = commands.add_parser("freeze", help="Create a reusable synthetic fixture pack")
    fixture.add_argument("--output", required=True, type=Path)
    fixture.add_argument("--id", required=True)
    check = commands.add_parser("verify-fixtures", help="Verify frozen save and authored files")
    check.add_argument("directory", type=Path)
    compare = commands.add_parser("compare", help="Report compatible batches without model calls")
    compare.add_argument("directories", type=Path, nargs="+")
    compare.add_argument("--output", type=Path, required=True)
    for name in ("run", "selftest"):
        command = commands.add_parser(name)
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--resume", action="store_true")
        command.add_argument("--fixtures", type=Path)
        command.add_argument("--retain-private-audit", action="store_true")
        if name == "run":
            command.add_argument("--config", type=Path)
            command.add_argument("--setup", required=True, action="append")
            command.add_argument("--scenario", action="append")
            command.add_argument("--repetitions", type=int, default=1)
            command.add_argument("--retries", type=int, default=2)
            command.add_argument("--allow-model-calls", action="store_true")
    for name in ("report", "replay"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "setups":
            print(json.dumps([s.model_dump() for s in setups(args.config)], indent=2))
        elif args.command == "scenarios":
            print(json.dumps([s.model_dump() for s in scenarios()], indent=2))
        elif args.command == "freeze":
            pack = freeze(args.output, args.id, source_identity())
            print(
                json.dumps(
                    {
                        "id": pack["id"],
                        "sha256": pack["sha256"],
                        "directory": str(args.output.resolve()),
                    },
                    indent=2,
                )
            )
        elif args.command == "verify-fixtures":
            print(verify(args.directory, source_identity())["sha256"])
        elif args.command == "report":
            print(report(args.directory))
        elif args.command == "compare":
            print(comparison(args.directories, args.output))
        elif args.command == "replay":
            _manifest, rows = load_results(args.directory)
            replay_suite = {s.id: s for s in scenarios()}
            for row in rows:
                scenario = replay_suite[row.scenario_id]
                if scenario.version != row.scenario_version:
                    raise ValueError("Replay requires the original scenario version")
                if row.outcome in {"error", "incomplete"}:
                    continue
                checks = evaluate(scenario, row.observations)
                if [(c.id, c.status) for c in checks] != [(c.id, c.status) for c in row.checks]:
                    raise ValueError(f"Replay disagrees with saved checks for {row.run_id}")
            print(f"Replayed {len(rows)} records without provider calls.")
        else:
            suite = scenarios()
            if args.command == "selftest":
                selected = [s for s in setups() if s.behavior in {"good", "bad"}]
                rows = run(
                    args.output,
                    selected,
                    suite,
                    resume=args.resume,
                    fixtures=args.fixtures,
                    retain_private_audit=args.retain_private_audit,
                )
                for row in rows:
                    scenario = next(s for s in suite if s.id == row.scenario_id)
                    if row.setup.behavior == "good" and row.outcome != "pass":
                        raise ValueError(f"Known-good {row.scenario_id} failed; inspect its record")
                    if row.setup.behavior == "bad" and not any(
                        c.id == scenario.expected_bad_check and c.status == "fail"
                        for c in row.checks
                    ):
                        raise ValueError(
                            f"Known-bad {row.scenario_id} did not fail its intended check"
                        )
                print(f"Self-test passed: {len(rows)} genuine owner runs; no model inference.")
            else:
                available = {s.id: s for s in setups(args.config)}
                if len(set(args.setup)) != len(args.setup) or set(args.setup) - available.keys():
                    raise ValueError("Choose distinct setup IDs from the setups command")
                if args.scenario:
                    if len(set(args.scenario)) != len(args.scenario) or set(args.scenario) - {
                        s.id for s in suite
                    }:
                        raise ValueError("Choose distinct scenario IDs from the scenarios command")
                    suite = [s for s in suite if s.id in args.scenario]
                rows = run(
                    args.output,
                    [available[s] for s in args.setup],
                    suite,
                    repetitions=args.repetitions,
                    retries=args.retries,
                    resume=args.resume,
                    allow_model_calls=args.allow_model_calls,
                    fixtures=args.fixtures,
                    retain_private_audit=args.retain_private_audit,
                )
                print(f"Recorded {len(rows)} runs.")
            print(report(args.output))
        return 0
    except (ValueError, ValidationError, OSError, KeyError, TypeError) as error:
        print(f"SylviaBench: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
