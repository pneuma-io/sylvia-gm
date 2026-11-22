"""Generate reference vectors for sylvia_app::builder_references' capture functions.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_builder_reference_vectors.py

The owner is a fake, as both implementations see it:
- `package_candidates` and `package_candidate` (an unknown id raises the store's
  `candidate_unavailable`);
- `install_package_archive`, which admits the archive and records the call;
- the package reads, served from the admitted archives.
Its candidates come from real archives (Riichi 0.1.1, the retained Rift snapshot, the current
bundled Rift archive), in several configurations. The catalog holds ruleset rows (ids, selection
ids, aliases, compatible Settings with and without candidates), bundled Plan rows, a saved Rift
sibling (saved first into the scratch storage root) and a custom Plan row.
Cases are `capture_content_references` over choice combinations, and `capture_launch_references`
over Interview-state chats. Plan payloads are compared by their canonical SHA-256.
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

from sylvia_gm.application import bundled_campaign_plans as bcp  # noqa: E402
from sylvia_gm.application import bundled_packages as bp  # noqa: E402
from sylvia_gm.application.builder_references import (  # noqa: E402
    capture_content_references,
    capture_launch_references,
)
from sylvia_gm.application.reusable_plans import save_reusable_rift_plan  # noqa: E402
from sylvia_gm.domain.authority import PackageRole, thaw_json_value  # noqa: E402
from sylvia_gm.domain.errors import CommandRejected  # noqa: E402
from sylvia_gm.domain.package_library import (  # noqa: E402
    CandidateStatus,
    PackageCandidate,
    canonical_json_bytes,
    closure_sha256,
)
from sylvia_gm.domain.session import (  # noqa: E402
    SessionContext,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive  # noqa: E402

OUTPUT = Path(__file__).with_name("builder_reference_vectors.json")
RIICHI = "rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package"
RETAINED = "campaign-plans/the-rift-awakening/setting.sylvia-gm-package"
NL = chr(10)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Archive:
    def __init__(self, data):
        self.data = data
        with admit_package_archive(io.BytesIO(data)) as admitted:
            self.lock = admitted.package_lock
            self.closure = closure_sha256(admitted.package_lock)
            self.records = {m.identity: json.loads(m.canonical_json) for m in admitted.manifests}
            self.files = {}
            for item in admitted.files:
                self.files.setdefault(item.manifest.identity, {})[item.file.path] = b"".join(
                    admitted.iter_file_chunks(item))


class Owner:
    def __init__(self, archives, names):
        self.archives = dict(archives)
        self.candidates = [self.candidate(name, archives[name]) for name in names]
        self.installs = []

    @staticmethod
    def candidate(name, archive):
        return PackageCandidate(candidate_id=f"cand-{name}", install_id=f"install-{name}",
                                status=CandidateStatus.ACTIVATED, closure_sha256=archive.closure,
                                package_lock=archive.lock, validation_report={})

    def package_candidates(self):
        return tuple(self.candidates)

    def package_candidate(self, candidate_id):
        if candidate_id == "cand-broken":
            raise CommandRejected("Package candidate record is invalid", code="candidate_invalid")
        for item in self.candidates:
            if item.candidate_id == candidate_id:
                return item
        raise CommandRejected("Package candidate is unavailable", code="candidate_unavailable")

    def install_package_archive(self, source, *, install_id):
        data = source.read()
        archive = Archive(data)
        self.archives[install_id] = archive
        self.installs.append([install_id, digest(data)])
        item = PackageCandidate(candidate_id=f"cand-{install_id}", install_id=install_id,
                                status=CandidateStatus.VALIDATED, closure_sha256=archive.closure,
                                package_lock=archive.lock, validation_report={})
        self.candidates.append(item)
        return item

    def _files(self, package_id, version, sha256):
        for archive in self.archives.values():
            if (package_id, version, sha256) in archive.files:
                return archive.files[(package_id, version, sha256)]
        raise CommandRejected("Installed package is unavailable")

    def package_file_paths(self, package_id, version, sha256, *, maximum_files=512):
        return tuple(sorted(self._files(package_id, version, sha256)))

    def package_text_file(self, package_id, version, sha256, path, *, maximum_bytes=65536):
        files = self._files(package_id, version, sha256)
        if path not in files:
            raise CommandRejected("Package source path is unavailable")
        return files[path].decode("utf-8")

    def package_record(self, package_id, version, sha256):
        for archive in self.archives.values():
            if (package_id, version, sha256) in archive.records:
                return archive.records[(package_id, version, sha256)]
        raise CommandRejected("Installed package is unavailable")


def result_shape(result):
    result = thaw_json_value(result)
    plan = result.get("plan")
    if plan is not None:
        plan["payload"] = digest(canonical_json_bytes(plan["payload"]))
        plan["readable_markdown"] = digest(plan["readable_markdown"].encode("utf-8"))
    return result


def outcome(call, owner):
    try:
        value = result_shape(call())
    except Exception as error:  # every class is behaviour
        return [type(error).__name__, str(error), getattr(error, "code", None), owner.installs]
    return ["ok", value, owner.installs]


def main() -> None:
    archives = {"riichi": Archive((ROOT / RIICHI).read_bytes()), "retained": Archive((ROOT / RETAINED).read_bytes()),
                "current": Archive(bp.bundled_rift_archive())}
    authority = bp.bundled_rift_authority()
    riichi_primary = next(e for e in archives["riichi"].lock.entries if e.role is PackageRole.PRIMARY)
    riichi_setting = next(e for e in archives["riichi"].lock.entries if e.role is not PackageRole.PRIMARY)
    retained_primary = next(e for e in archives["retained"].lock.entries if e.role is PackageRole.PRIMARY)
    retained_setting = next(e for e in archives["retained"].lock.entries if e.role is not PackageRole.PRIMARY)
    with tempfile.TemporaryDirectory() as scratch:
        storage = Path(scratch) / "storage"
        storage.mkdir()
        rift = bp.bundled_rift_campaign_plan()
        sibling = save_reusable_rift_plan(storage, operation_id="builder-references", title="Saved Rift",
                                          source_payload=rift.payload, source_markdown=rift.readable_markdown)
        bundled_rows = [row for row in bcp.bundled_plan_catalog() if row["id"] in {"a-debt-in-iron", "the-bell-below"}]
        catalog = [
            {"kind": "ruleset", "id": authority.ruleset.package_id, "selection_id": "srd", "selection_aliases": ["srd-lite"],
             "version": authority.ruleset.version, "content_sha256": authority.ruleset.content_sha256,
             "compatible_settings": [{"id": authority.setting.package_id, "version": authority.setting.version,
                                      "content_sha256": authority.setting.content_sha256, "candidate_id": None}]},
            {"kind": "ruleset", "id": riichi_primary.package_id, "version": riichi_primary.version,
             "content_sha256": riichi_primary.sha256,
             "compatible_settings": [{"id": riichi_setting.package_id, "version": riichi_setting.version,
                                      "content_sha256": riichi_setting.sha256, "candidate_id": "cand-riichi"}]},
            {"kind": "ruleset", "id": retained_primary.package_id, "selection_id": "srd-retained",
             "version": retained_primary.version, "content_sha256": retained_primary.sha256,
             "compatible_settings": [{"id": retained_setting.package_id, "version": retained_setting.version,
                                      "content_sha256": retained_setting.sha256}]},
            *[thaw_json_value(row) for row in bundled_rows],
            thaw_json_value(sibling.catalog_payload()),
            {"kind": "campaign_plan", "id": "custom-plan", "content_sha256": "0" * 64},
        ]
        severed = authority.setting.package_id
        choices = [
            (None, None, None, None), ("srd", None, None, None), ("srd-lite", severed, None, None),
            (authority.ruleset.package_id, severed, None, None), ("srd", "pneuma.other", None, None),
            (riichi_primary.package_id, riichi_setting.package_id, None, None), (riichi_primary.package_id, None, None, None),
            (None, riichi_setting.package_id, "cand-riichi", None), ("srd", riichi_setting.package_id, "cand-riichi", None),
            ("cand-riichi", riichi_setting.package_id, None, None), ("cand-missing", None, None, None), ("cand-broken", None, None, None),
            ("unknown", None, None, None), (5, None, None, None), ("srd", severed, "cand-missing", None),
            ("srd-retained", retained_setting.package_id, None, None),
            ("srd", severed, None, "a-debt-in-iron"), ("srd", severed, None, "the-bell-below"),
            ("srd", severed, None, "the-rift-awakening"), ("srd-retained", retained_setting.package_id, None, "the-rift-awakening"),
            ("srd-retained", retained_setting.package_id, None, sibling.artifact_id), ("srd", severed, None, sibling.artifact_id),
            ("srd", severed, None, "custom-plan"), ("srd", severed, None, "missing-plan"), (None, None, None, "a-debt-in-iron"),
            ("srd", "pneuma.other", None, "a-debt-in-iron"),
        ]
        owners = {"empty": [], "riichi": ["riichi"], "riichi_retained": ["riichi", "retained"], "all": ["riichi", "retained", "current"]}
        cases = []
        for owner_name, names in owners.items():
            for ruleset_id, setting_id, setting_candidate_id, plan_id in choices:
                owner = Owner(archives, names)
                cases.append({"owner": owner_name, "choices": [ruleset_id, setting_id, setting_candidate_id, plan_id],
                              "outcome": outcome(lambda: capture_content_references(
                                  owner, storage, catalog, ruleset_id=ruleset_id, setting_id=setting_id,
                                  setting_candidate_id=setting_candidate_id, plan_id=plan_id), owner)})
        launches = []
        interview = [
            ("answer-ruleset", {"interview_value": {"candidate_id": "srd"}}),
            ("answer-setting", {"interview_value": {"setting_id": severed}}),
            ("answer-plan", {"interview_value": {"plan_id": "a-debt-in-iron"}}),
            ("answer-riichi", {"interview_value": {"candidate_id": riichi_primary.package_id}}),
            ("answer-bad", {"interview_value": ["x"]}),
        ]
        state_sets = {
            "none": [],
            "srd": [{"kind": "campaign_interview_state", "answers": {"ruleset": "answer-ruleset", "setting": "answer-setting"}}],
            "srd_plan": [{"kind": "campaign_interview_state", "answers": {"ruleset": "answer-ruleset", "setting": "answer-setting", "plan": "answer-plan"}}],
            "latest_wins": [{"kind": "campaign_interview_state", "answers": {"ruleset": "answer-ruleset"}},
                            {"kind": "campaign_interview_state", "answers": {"ruleset": "answer-riichi"}}],
            "answers_list": [{"kind": "campaign_interview_state", "answers": ["answer-ruleset"]}],
            "bad_value": [{"kind": "campaign_interview_state", "answers": {"ruleset": "answer-bad"}}],
            "missing_answer": [{"kind": "campaign_interview_state", "answers": {"ruleset": "answer-none"}}],
            "other_kind": [{"kind": "campaign_interview_draft", "answers": {"ruleset": "answer-ruleset"}}],
        }
        from sylvia_gm.domain.authority import WriterRole
        session = SessionRecord(session_id="builder-session", campaign_id=None, role=WriterRole.BUILDER,
                                provider_id="provider", model_id="model")
        for state_name, states in state_sets.items():
            messages = [SessionMessage("builder-session", index + 1, message_id, SessionMessageKind.USER, content)
                        for index, (message_id, content) in enumerate(interview)]
            for index, state in enumerate(states):
                messages.append(SessionMessage("builder-session", len(messages) + 1, f"state-{index}",
                                               SessionMessageKind.EVIDENCE, state))
            context = SessionContext(session=session, messages=tuple(messages))
            for owner_name in ("riichi", "all"):
                owner = Owner(archives, owners[owner_name])
                launches.append({"states": state_name, "owner": owner_name, "outcome": outcome(
                    lambda: capture_launch_references(owner, storage, context, catalog), owner)})
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "riichi": RIICHI, "retained": RETAINED,
               "sibling": sibling.artifact_id, "catalog": catalog, "owners": owners, "interview": interview,
               "state_sets": state_sets, "cases": cases, "launches": launches}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL
    OUTPUT.write_text(text, encoding="utf-8", newline=NL)
    kinds = {}
    for case in cases + launches:
        result = case["outcome"]
        key = "ok" if result[0] == "ok" else f"{result[0]} {result[1][:60]}"
        kinds[key] = kinds.get(key, 0) + 1
    print(len(cases), "content cases,", len(launches), "launches,", len(text), "bytes")
    for key in sorted(kinds):
        print(" ", kinds[key], ascii(key))


if __name__ == "__main__":
    main()
