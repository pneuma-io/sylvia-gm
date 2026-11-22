"""Generate reference vectors for sylvia_app::interview_content (application/interview_content.py).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_interview_content_vectors.py

Each case runs `resolve_interview_content(owner, None, selections)` against a fake owner
holding the candidates named in `candidates` (`package_candidate` raises `CommandRejected` with
`candidate_unavailable` for any other identity, or another code for "broken"). The bundled
Rift authority is the shipped one, so a candidate can carry its exact closure.

Where Python reaches code the port has not got yet, the outcome is `["pending", function]`:
`bundled_plan` and `bundled_setting_snapshot` (bundled_campaign_plans) and the owner's
`install_package_archive` are replaced here by a marker that records the call, so the Rust port
must stop at exactly the same calls. Every other outcome is the result's candidate and Setting,
or the exception class, text and code.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import sylvia_gm.application.interview_content as interview  # noqa: E402
from sylvia_gm.application.bundled_packages import bundled_rift_authority  # noqa: E402
from sylvia_gm.domain.authority import PackageDependency, PackageLock, PackageLockEntry, PackageRole  # noqa: E402
from sylvia_gm.domain.errors import CommandRejected  # noqa: E402
from sylvia_gm.domain.package_library import CandidateStatus, PackageCandidate  # noqa: E402

OUTPUT = Path(__file__).with_name("interview_content_vectors.json")
SHA = "a" * 64
RIFT = bundled_rift_authority()


class Pending(Exception):
    pass


def marker(name):
    def call(*_args, **_kwargs):
        raise Pending(name)
    return call


interview.bundled_plan = marker("bundled_campaign_plans.bundled_plan")
interview.bundled_setting_snapshot = marker("bundled_campaign_plans.bundled_setting_snapshot")


def entry(package_id, role, dependencies=(), version="1.0.0"):
    return {"id": package_id, "version": version, "role": role, "dependencies": [list(item) for item in dependencies]}


def candidate_from(spec) -> PackageCandidate:
    entries = []
    for order, item in enumerate(spec["entries"]):
        entries.append(PackageLockEntry(
            package_id=item["id"], version=item["version"], sha256=SHA,
            role=PackageRole.PRIMARY if item["role"] == "primary" else PackageRole.DEPENDENCY, load_order=order,
            dependencies=tuple(PackageDependency(package_id=d[0], version=d[1], sha256=SHA, kind=d[2], namespace=d[3])
                               for d in item["dependencies"]),
            capabilities=()))
    return PackageCandidate(candidate_id=spec["candidate_id"], install_id="install-" + spec["candidate_id"],
                            status=CandidateStatus.VALIDATED, closure_sha256=spec["closure"],
                            package_lock=PackageLock(tuple(entries)), validation_report={})


class Owner:
    def __init__(self, specs):
        self.candidates = [candidate_from(spec) for spec in specs]

    def package_candidate(self, candidate_id):
        if candidate_id == "broken":
            raise CommandRejected("Package store is unavailable", code="store_unavailable")
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise CommandRejected("Package candidate is unavailable", code="candidate_unavailable")

    def package_candidates(self):
        return tuple(self.candidates)

    install_package_archive = staticmethod(marker("install_package_archive"))


RULESET = entry("pneuma.rules", "primary")
SETTING = entry("pneuma.world", "dependency", [("pneuma.rules", "1.0.0", "ruleset_snapshot", "ruleset")])
LEGACY = entry("pneuma.rules", "primary", [("pneuma.old-world", "1.0.0", "campaign_setting", "campaign_setting")])
CANDIDATES = {
    "rules": {"candidate_id": "rules", "closure": "1" * 64, "entries": [RULESET]},
    "world": {"candidate_id": "world", "closure": "2" * 64, "entries": [RULESET, SETTING]},
    "legacy": {"candidate_id": "legacy", "closure": "3" * 64,
               "entries": [LEGACY, entry("pneuma.old-world", "dependency")]},
    "other": {"candidate_id": "other", "closure": "4" * 64, "entries": [entry("pneuma.other", "primary")]},
    "archived": {"candidate_id": "archived", "closure": "5" * 64, "entries": [entry("wotc.srd.5.2.1", "primary")]},
    "rift": {"candidate_id": "rift", "closure": RIFT.closure_sha256, "entries": [
        entry(RIFT.ruleset.package_id, "primary", version=RIFT.ruleset.version),
        entry(RIFT.setting.package_id, "dependency", [(RIFT.ruleset.package_id, RIFT.ruleset.version,
                                                       "ruleset_snapshot", "ruleset")], version=RIFT.setting.version)]},
}
ALL = list(CANDIDATES)


def outcome(owner_names, selections):
    owner = Owner([CANDIDATES[name] for name in owner_names])
    try:
        content = interview.resolve_interview_content(owner, None, selections)
    except Pending as marked:
        return ["pending", str(marked)]
    except Exception as error:  # every class is behaviour
        return [type(error).__name__, str(error), getattr(error, "code", None)]
    return ["ok", content.candidate.candidate_id if content.candidate else None, content.setting_id,
            content.plan_contribution]


def main() -> None:
    rift_rules, rift_world = RIFT.ruleset.package_id, RIFT.setting.package_id
    selections = [
        {}, {"ruleset": {}}, {"ruleset": {"candidate_id": None}}, {"setting": {"setting_id": "x"}},
        {"plan": {"plan_id": "p"}}, {"ruleset": {"candidate_id": ""}}, {"ruleset": {"candidate_id": 5}},
        {"ruleset": {"candidate_id": "rules"}}, {"ruleset": {"candidate_id": "missing"}},
        {"ruleset": {"candidate_id": "broken"}}, {"ruleset": {"candidate_id": "archived"}},
        {"ruleset": {"candidate_id": "wotc.srd.5.2.1"}}, {"ruleset": {"candidate_id": "rules"}, "plan": {"plan_id": "p"}},
        {"ruleset": {"candidate_id": "rules"}, "plan": {"plan_id": 7}},
        {"ruleset": {"candidate_id": "rules"}, "plan": {"plan_id": None}},
        {"ruleset": {"candidate_id": "rules"}, "setting": {"setting_id": "pneuma.world"}},
        {"ruleset": {"candidate_id": "world"}, "setting": {"setting_id": "pneuma.world"}},
        {"ruleset": {"candidate_id": "rules"}, "setting": {"setting_id": "pneuma.world", "candidate_id": "world"}},
        {"ruleset": {"candidate_id": "rules"}, "setting": {"setting_id": "pneuma.world", "candidate_id": "gone"}},
        {"ruleset": {"candidate_id": "rules"}, "setting": {"setting_id": "pneuma.world", "candidate_id": 3}},
        {"ruleset": {"candidate_id": "rules"}, "setting": {"candidate_id": "world"}},
        {"ruleset": {"candidate_id": "rules"}, "setting": {"candidate_id": "other"}},
        {"ruleset": {"candidate_id": "rules"}, "setting": {"setting_id": 5}},
        {"ruleset": {"candidate_id": "legacy"}, "setting": {"setting_id": "pneuma.old-world"}},
        {"ruleset": {"candidate_id": "world"}, "setting": {"setting_id": "pneuma.elsewhere"}},
        {"ruleset": {"candidate_id": rift_rules}, "setting": {"setting_id": rift_world}},
        {"ruleset": {"candidate_id": rift_rules}, "setting": {"setting_id": "pneuma.elsewhere"}},
        {"ruleset": {"candidate_id": "pneuma.rules"}, "setting": {"setting_id": "pneuma.world"}},
        {"ruleset": {"candidate_id": "pneuma.rules"}},
        {"ruleset": "rules"}, {"ruleset": None}, {"ruleset": {"candidate_id": "rules"}, "setting": None},
        {"ruleset": {"candidate_id": "rules"}, "setting": "x"}, {"ruleset": {"candidate_id": "rules"}, "plan": []},
    ]
    owners = [ALL, ["rules", "world", "legacy", "other", "archived"], []]
    cases = []
    for names in owners:
        for selection in selections:
            cases.append({"owner": names, "selections": selection, "outcome": outcome(names, selection)})
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "sha": SHA, "candidates": CANDIDATES,
               "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, indent=0) + chr(10), encoding="utf-8", newline=chr(10))
    kinds = {}
    for case in cases:
        key = case["outcome"][0] if case["outcome"][0] in ("ok",) else " ".join(map(str, case["outcome"][:2]))
        kinds[key[:80]] = kinds.get(key[:80], 0) + 1
    print(len(cases), "cases")
    for key in sorted(kinds):
        print(" ", kinds[key], key)


if __name__ == "__main__":
    main()
