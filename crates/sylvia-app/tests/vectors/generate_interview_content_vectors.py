"""Generate reference vectors for sylvia_app::interview_content (application/interview_content.py).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_interview_content_vectors.py

Each case runs `resolve_interview_content(owner, storage_root, selections)` against a fake owner
holding the candidates named in `candidates` (`package_candidate` raises `CommandRejected` with
`candidate_unavailable` for any other identity, or another code for "broken"). The bundled
Rift authority is the shipped one, so a candidate can carry its exact closure.

`install_package_archive` admits the archive and adds its candidate (`cand-<install id>`), and
the owner's package reads are served from the archives it has installed, as builder_references'
fake owner serves them. A case's `storage` names the storage root: none, or one holding a saved
Rift sibling (`save_reusable_rift_plan`). Each outcome is the result's candidate, Setting and Plan
contribution, or the exception class, text and code, with the installs made either way.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import sylvia_gm.application.interview_content as interview  # noqa: E402
from sylvia_gm.application.bundled_packages import bundled_rift_authority, bundled_rift_campaign_plan  # noqa: E402
from sylvia_gm.application.reusable_plans import save_reusable_rift_plan  # noqa: E402
from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.domain.package_library import closure_sha256  # noqa: E402
from sylvia_gm.infrastructure.package_archive import admit_package_archive  # noqa: E402
from sylvia_gm.domain.authority import PackageDependency, PackageLock, PackageLockEntry, PackageRole  # noqa: E402
from sylvia_gm.domain.errors import CommandRejected  # noqa: E402
from sylvia_gm.domain.package_library import CandidateStatus, PackageCandidate  # noqa: E402

OUTPUT = Path(__file__).with_name("interview_content_vectors.json")
SHA = "a" * 64
RIFT = bundled_rift_authority()


def admitted(data):
    with admit_package_archive(io.BytesIO(data)) as archive:
        records = {m.identity: json.loads(m.canonical_json) for m in archive.manifests}
        files = {}
        for item in archive.files:
            files.setdefault(item.manifest.identity, {})[item.file.path] = b"".join(archive.iter_file_chunks(item))
        return archive.package_lock, closure_sha256(archive.package_lock), records, files


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
        self.installs = []
        self.records = {}
        self.files = {}

    def install_package_archive(self, source, *, install_id):
        data = source.read()
        lock, closure, records, files = admitted(data)
        self.installs.append([install_id, hashlib.sha256(data).hexdigest()])
        self.records.update(records)
        self.files.update(files)
        item = PackageCandidate(candidate_id=f"cand-{install_id}", install_id=install_id,
                                status=CandidateStatus.VALIDATED, closure_sha256=closure, package_lock=lock,
                                validation_report={})
        self.candidates.append(item)
        return item

    def package_file_paths(self, package_id, version, sha256, *, maximum_files=512):
        if (package_id, version, sha256) not in self.files:
            raise CommandRejected("Installed package is unavailable")
        return tuple(sorted(self.files[(package_id, version, sha256)]))

    def package_text_file(self, package_id, version, sha256, path, *, maximum_bytes=65536):
        files = self.files.get((package_id, version, sha256))
        if files is None:
            raise CommandRejected("Installed package is unavailable")
        if path not in files:
            raise CommandRejected("Package source path is unavailable")
        return files[path].decode("utf-8")

    def package_record(self, package_id, version, sha256):
        if (package_id, version, sha256) not in self.records:
            raise CommandRejected("Installed package is unavailable")
        return self.records[(package_id, version, sha256)]

    def package_candidate(self, candidate_id):
        if candidate_id == "broken":
            raise CommandRejected("Package store is unavailable", code="store_unavailable")
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise CommandRejected("Package candidate is unavailable", code="candidate_unavailable")

    def package_candidates(self):
        return tuple(self.candidates)


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
    "srd-only": {"candidate_id": "srd-only", "closure": "6" * 64, "entries": [
        entry(RIFT.ruleset.package_id, "primary", version=RIFT.ruleset.version)]},
    "rift": {"candidate_id": "rift", "closure": RIFT.closure_sha256, "entries": [
        entry(RIFT.ruleset.package_id, "primary", version=RIFT.ruleset.version),
        entry(RIFT.setting.package_id, "dependency", [(RIFT.ruleset.package_id, RIFT.ruleset.version,
                                                       "ruleset_snapshot", "ruleset")], version=RIFT.setting.version)]},
}
ALL = list(CANDIDATES)


def outcome(owner_names, selections, storage_root=None):
    owner = Owner([CANDIDATES[name] for name in owner_names])
    try:
        content = interview.resolve_interview_content(owner, storage_root, selections)
    except Exception as error:  # every class is behaviour
        return [type(error).__name__, str(error), getattr(error, "code", None), owner.installs]
    contribution = None if content.plan_contribution is None else thaw_json_value(content.plan_contribution)
    return ["ok", content.candidate.candidate_id if content.candidate else None, content.setting_id,
            contribution, owner.installs]


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
    srd, lands = "sylvia.srd-lite.bg3", "pneuma.severed-lands"
    riichi, club = "pneuma.riichi-mahjong", "pneuma.riichi-club"
    with tempfile.TemporaryDirectory() as scratch:
        storage = Path(scratch) / "storage"
        storage.mkdir()
        rift_plan = bundled_rift_campaign_plan()
        sibling = save_reusable_rift_plan(storage, operation_id="interview-vectors", title="Saved Rift",
                                          source_payload=thaw_json_value(rift_plan.payload),
                                          source_markdown=rift_plan.readable_markdown).artifact_id
        selections += [
            {"ruleset": {"candidate_id": srd}, "setting": {"setting_id": lands}, "plan": {"plan_id": "last-cart-through"}},
            {"ruleset": {"candidate_id": srd}, "setting": {"setting_id": lands}, "plan": {"plan_id": "a-debt-in-iron"}},
            {"ruleset": {"candidate_id": srd}, "setting": {"setting_id": lands}, "plan": {"plan_id": "the-rift-awakening"}},
            {"ruleset": {"candidate_id": srd}, "setting": {"setting_id": lands}, "plan": {"plan_id": "rift-awakening"}},
            {"ruleset": {"candidate_id": srd}, "setting": {"setting_id": "pneuma.elsewhere"},
             "plan": {"plan_id": "last-cart-through"}},
            {"ruleset": {"candidate_id": srd}, "plan": {"plan_id": "last-cart-through"}},
            {"ruleset": {"candidate_id": riichi}, "setting": {"setting_id": club}, "plan": {"plan_id": "riichi-quick-test"}},
            {"ruleset": {"candidate_id": riichi}, "setting": {"setting_id": lands}, "plan": {"plan_id": "riichi-quick-test"}},
            {"ruleset": {"candidate_id": srd}, "setting": {"setting_id": lands}, "plan": {"plan_id": sibling}},
            {"ruleset": {"candidate_id": srd}, "setting": {"setting_id": lands}, "plan": {"plan_id": "no-such-plan"}},
            {"ruleset": {"candidate_id": srd}, "setting": {"setting_id": lands}},
            {"ruleset": {"candidate_id": srd}, "setting": {"setting_id": lands, "candidate_id": "rift"}},
            {"ruleset": {"candidate_id": riichi}, "setting": {"setting_id": club}},
            {"ruleset": {"candidate_id": riichi}, "setting": {"setting_id": lands}},
            {"ruleset": {"candidate_id": "srd-only"}, "setting": {"setting_id": lands}},
            {"ruleset": {"candidate_id": "srd-only"}, "setting": {"setting_id": club}},
        ]
        owners = [ALL, ["rules", "world", "legacy", "other", "archived"], [], ["srd-only"]]
        cases = []
        for names in owners:
            for selection in selections:
                for label, root in (("none", None), ("saved", storage)):
                    if label == "saved" and "plan" not in selection:
                        continue
                    cases.append({"owner": names, "storage": label, "selections": selection,
                                  "outcome": outcome(names, selection, root)})
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "sha": SHA, "candidates": CANDIDATES,
               "sibling": sibling, "cases": cases}
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
