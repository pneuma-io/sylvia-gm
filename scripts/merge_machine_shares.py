"""Join the results of one complete check run that was split across machines.

Each machine ran ``check_supported_journeys.py --machine-share I/N`` and kept its output
folder. This command checks that the folders are the N shares of one run of one source, then
writes a single receipt. It never runs tests.

Exit code 0: every share passed. 1: the set is valid and some share failed. 2: the folders
are not a complete, consistent set of shares, and nothing is merged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# Fields that must be identical in every share of one run.
SAME_IN_EVERY_SHARE = (
    "revision", "source_sha256", "platform", "qualification_kind", "qualification_mode",
)


class ShareSetError(ValueError):
    """The folders are not the complete set of shares of one run."""


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ShareSetError(f"{path} could not be read: {error}") from error


def _load(folder):
    summary = _read(folder / "summary.json")
    share = summary.get("machine_share") if isinstance(summary, dict) else None
    if not isinstance(summary, dict) or summary.get("status") != "completed":
        raise ShareSetError(f"{folder} is not a completed run")
    if not isinstance(share, dict):
        raise ShareSetError(f"{folder} is not a machine share of a complete run")
    plan = _read(folder / "machine-share.json")
    if not isinstance(plan, dict) or (plan.get("index"), plan.get("count")) != (
        share.get("index"), share.get("count")
    ):
        raise ShareSetError(f"{folder}: machine-share.json does not match summary.json")
    required = plan.get("required_cases")
    assigned = plan.get("assigned_cases")
    if not isinstance(required, list) or not isinstance(assigned, list):
        raise ShareSetError(f"{folder}: machine-share.json does not list its cases")
    digest = hashlib.sha256("\n".join(sorted(required)).encode("utf-8")).hexdigest()
    if digest != plan.get("required_cases_sha256") or digest != share.get("required_cases_sha256"):
        raise ShareSetError(f"{folder}: the required case list does not match its recorded SHA-256")
    if len(assigned) != share.get("assigned_cases"):
        raise ShareSetError(f"{folder}: the assigned case count does not match summary.json")
    if summary.get("unchanged_source") is not True:
        raise ShareSetError(f"{folder}: the source changed during that share's run")
    return {"folder": folder, "summary": summary, "share": share, "plan": plan}


def merge(folders):
    """Return the merged receipt, or raise ShareSetError naming the first problem."""
    if not folders:
        raise ShareSetError("no share folders were given")
    shares = [_load(Path(folder)) for folder in folders]
    first = shares[0]
    count = first["share"]["count"]
    for item in shares[1:]:
        if item["share"]["count"] != count:
            raise ShareSetError(
                f"{item['folder']} is a share of {item['share']['count']}, "
                f"but {first['folder']} is a share of {count}"
            )
        for field in SAME_IN_EVERY_SHARE:
            if item["summary"].get(field) != first["summary"].get(field):
                raise ShareSetError(
                    f"{item['folder']} and {first['folder']} differ in {field}: "
                    f"{item['summary'].get(field)!r} and {first['summary'].get(field)!r}"
                )
        if item["share"]["required_cases_sha256"] != first["share"]["required_cases_sha256"]:
            raise ShareSetError(
                f"{item['folder']} and {first['folder']} were split from different case lists"
            )
        if item["summary"].get("excluded_cases") != first["summary"].get("excluded_cases"):
            raise ShareSetError(
                f"{item['folder']} and {first['folder']} excluded different cases"
            )
    indexes = sorted(item["share"]["index"] for item in shares)
    if indexes != list(range(1, count + 1)):
        missing = sorted(set(range(1, count + 1)) - set(indexes))
        repeated = sorted({index for index in indexes if indexes.count(index) > 1})
        raise ShareSetError(
            f"expected shares 1 to {count} once each; "
            f"missing {missing or 'none'}, repeated {repeated or 'none'}"
        )
    required = set(first["plan"]["required_cases"])
    seen: set[str] = set()
    for item in shares:
        assigned = set(item["plan"]["assigned_cases"])
        if len(assigned) != len(item["plan"]["assigned_cases"]) or assigned & seen:
            raise ShareSetError(f"{item['folder']} repeats a case that another share also ran")
        seen |= assigned
    if seen != required:
        raise ShareSetError(
            f"the shares do not cover the required cases exactly: {len(required - seen)} "
            f"not assigned, {len(seen - required)} not required"
        )
    with_ui = [item for item in shares if item["share"].get("ui_controls_included")]
    if len(with_ui) != 1:
        raise ShareSetError(
            f"the browser tests must be in exactly one share, not {len(with_ui)}"
        )

    shares.sort(key=lambda item: item["share"]["index"])
    counts: dict[str, int] = {}
    expected_failures = []
    for item in shares:
        for name, value in item["summary"].get("python_case_counts", {}).items():
            counts[name] = counts.get(name, 0) + value
        expected_failures.extend(item["summary"].get("expected_failures", []))
    elapsed = [item["summary"].get("elapsed_seconds", 0) for item in shares]
    summary = first["summary"]
    return {
        "status": "completed",
        "passed": all(item["summary"].get("passed") is True for item in shares),
        "complete_gate": True,
        "gate_kind": f"complete deterministic, assembled from {count} machine shares",
        "machine_shares": [
            {
                "index": item["share"]["index"],
                "passed": item["summary"].get("passed") is True,
                "assigned_cases": item["share"]["assigned_cases"],
                "ui_controls_included": bool(item["share"].get("ui_controls_included")),
                "elapsed_seconds": item["summary"].get("elapsed_seconds"),
                "jobs": item["summary"].get("jobs"),
                "total_case_workers": item["summary"].get("total_case_workers"),
            }
            for item in shares
        ],
        "elapsed_seconds_longest_share": max(elapsed),
        "elapsed_seconds_total": round(sum(elapsed), 2),
        "python_case_counts": counts,
        "expected_failures": sorted(expected_failures, key=lambda entry: entry.get("nodeid", "")),
        "excluded_cases": summary.get("excluded_cases", []),
        "excluded_case_counts": summary.get("excluded_case_counts"),
        "total_required_cases": first["share"]["total_required_cases"],
        "required_cases_sha256": first["share"]["required_cases_sha256"],
        "installed_acceptance_run": False,
        **{field: summary.get(field) for field in SAME_IN_EVERY_SHARE},
        "unchanged_source": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("shares", nargs="+", type=Path, metavar="SHARE_DIR",
                        help="Output folder of one --machine-share run")
    parser.add_argument("--output", type=Path, required=True,
                        help="Where to write the merged receipt (JSON)")
    args = parser.parse_args(argv)
    try:
        merged = merge(args.shares)
    except ShareSetError as error:
        print(f"NOT MERGED: {error}", file=sys.stderr)
        return 2
    args.output.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    counts = merged["python_case_counts"]
    failed = [share["index"] for share in merged["machine_shares"] if not share["passed"]]
    print(
        f"Complete run from {len(merged['machine_shares'])} machine shares: "
        + ("PASS" if merged["passed"] else f"FAIL: share {', '.join(map(str, failed))}")
        + f" ({counts.get('passed', 0)} passed, {counts.get('failed', 0)} failed, "
        f"{counts.get('errors', 0)} errors, {counts.get('unexpected_skips', 0)} unexpected skips; "
        f"longest share {merged['elapsed_seconds_longest_share'] / 60:.0f} min)"
    )
    print(f"Merged receipt: {args.output}")
    return 0 if merged["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
