"""Generate reference vectors for sylvia_app's package_materialization and bundled_packages.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_bundled_vectors.py

Records:
- the SHA-256 of every bundled archive (`bundled_srd_archive`, `bundled_rift_archive`,
  `bundled_riichi_archive` and `bundled_ruleset_update_archive` for each shipped Ruleset), the
  Rift authority, participant declaration, interview questions, Builder source catalog and
  source reads, all read from the repository's package sources;
- `materialize_package_archive` and `snapshot_record` on synthetic drafts and their mutations
  (archive SHA-256 or the exception class and message).
The ZIP bytes are zlib's, so the vectors record `zlib.ZLIB_RUNTIME_VERSION`.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application import bundled_packages as bundled  # noqa: E402
from sylvia_gm.application.package_materialization import (  # noqa: E402
    NormalizedPackageDraft,
    materialize_package_archive,
    snapshot_record,
)
from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.domain.package_library import manifest_from_record  # noqa: E402

OUTPUT = Path(__file__).with_name("bundled_vectors.json")
rng = random.Random(20261127)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def outcome(call, project):
    try:
        result = call()
    except Exception as error:  # every class is behaviour
        return [type(error).__name__, str(error)]
    return ["ok", project(result)]


def bundled_facts():
    authority = bundled.bundled_rift_authority()
    declaration = bundled.bundled_rift_participant_declaration()
    contribution = bundled.bundled_rift_interview_contribution()
    reads = ["campaign-plan/example/plan.json", "campaign-plan/example/plan.md", "ruleset/identity.json",
             *[f"setting/{path}" for path in sorted(authority.setting_paths)[:40]], "setting/missing.md", "lore/x.md",
             "setting/"]
    return {
        "srd_archive": sha(bundled.bundled_srd_archive()),
        "rift_archive": sha(bundled.bundled_rift_archive()),
        "riichi_archive": sha(bundled.bundled_riichi_archive()),
        "update_archives": {package_id: (None if (value := bundled.bundled_ruleset_update_archive(package_id)) is None
                                         else sha(value))
                            for package_id in ("sylvia.srd-lite.bg3", "pneuma.riichi-mahjong", "pneuma.riichi-club", "x")},
        "srd_version": bundled.bundled_srd_version(),
        "authority": {
            "ruleset": authority.ruleset.as_payload(), "setting": authority.setting.as_payload(),
            "closure_sha256": authority.closure_sha256, "setting_paths": sorted(authority.setting_paths),
            "setting_knowledge_ids": sorted(authority.setting_knowledge_ids),
        },
        "participant_declaration": {
            "kind": declaration.kind, "schema_id": declaration.schema_id, "schema_sha256": declaration.schema_sha256,
        },
        "interview_contribution": None if contribution is None else thaw_json_value(contribution.as_payload()),
        "source_catalog": {query: list(bundled.bundled_rift_source_catalog(query))
                           for query in ("", "lore", "  NPCS ", "zzz", "plan.")},
        "source_reads": {path: outcome(lambda: bundled.read_bundled_rift_source(path), lambda text: sha(text.encode()))
                         for path in reads},
    }


# --- synthetic drafts -----------------------------------------------------------------------

RIICHI = ROOT / "rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package"


def riichi_drafts():
    """The shipped Riichi archive's two records and payloads, as materialization inputs."""
    import zipfile
    with zipfile.ZipFile(RIICHI) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = manifest["packages"]
        payloads = {}
        for record in records:
            prefix = f"packages/{record['id']}/"
            payloads[record["id"]] = {name[len(prefix):]: archive.read(name).decode("utf-8")
                                      for name in archive.namelist() if name.startswith(prefix)}
    return manifest["format"], records, payloads


FORMAT, RECORDS, PAYLOADS = riichi_drafts()
RULESET, SETTING = RECORDS


def drafts_from(records, payloads, version=4):
    return [NormalizedPackageDraft(record, {path: value.encode() for path, value in (payloads.get(record.get("id"), {}) if isinstance(record.get("id"), str) else {}).items()},
                                   version) for record in records]


def apply(document, edits):
    """Edits on the base inputs: set, delete, reverse a list, or copy one value to another path."""
    document = copy.deepcopy(document)

    def walk(path):
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        return parent

    for edit in edits:
        op, path = edit[0], edit[1]
        if op == "set":
            walk(path)[path[-1]] = copy.deepcopy(edit[2])
        elif op == "delete":
            del walk(path)[path[-1]]
        elif op == "reverse":
            walk(path)[path[-1]].reverse()
        elif op == "copy":
            walk(edit[2])[edit[2][-1]] = copy.deepcopy(walk(path)[path[-1]])
    return document


def materialize_cases():
    cases = []
    project = lambda m: {"archive": sha(m.archive.read()), "closure": m.closure_sha256,  # noqa: E731
                         "manifests": [item.package_id for item in m.manifests]}
    base = {"records": [RULESET, SETTING], "payloads": PAYLOADS, "version": FORMAT}
    rid = RULESET["id"]
    artifact = {"kind": "player_character", "schema_id": "pc", "schema": {"type": "object"}}

    def run(edits):
        inputs = apply(base, edits)

        def call():
            return materialize_package_archive(drafts_from(inputs["records"], inputs["payloads"], inputs["version"]))
        return {"edits": edits, "outcome": outcome(call, project)}

    variants = [
        [],
        [["delete", ["records", 1]]],
        [["reverse", ["records"]]],
        [["set", ["records"], []]],
        [["copy", ["records", 0], ["records", 1]]],
        [["set", ["version"], 9]],
        [["set", ["version"], 3]],
        [["set", ["payloads", rid], {}]],
        [["set", ["payloads", rid], {"a.md": "x", "A.md": "y"}]],
        [["set", ["payloads", rid], {"../x.md": "x"}]],
        [["set", ["payloads", rid, "caf" + chr(0xE9) + ".md"], "x"]],
        [["set", ["payloads", rid, "zz/added.md"], "line\r\nline\n"]],
        [["set", ["records", 0, "participant_artifacts"], [artifact]]],
        [["set", ["records", 0, "participant_artifacts"], [{**artifact, "schema_sha256": "0" * 64}]]],
        [["set", ["records", 0, "participant_artifacts"], "x"]],
        [["set", ["records", 0, "participant_artifacts"], [{"schema": 1}]]],
        [["set", ["records", 0, "participant_artifacts"], [{**artifact, "initial_state_calculations": [{"set": 1}]}]]],
        [["set", ["records", 0, "participant_artifacts"], [{**artifact, "entity_state_schema": {"type": "object"},
                                                          "initial_state_calculations": None}]]],
        [["set", ["records", 1, "dependencies", 0, "version"], "2.0.0"]],
        [["set", ["records", 1, "dependencies", 0, "content_sha256"], "0" * 64]],
        [["delete", ["records", 1, "dependencies", 0, "content_sha256"]]],
        [["set", ["records", 1, "dependencies"], "x"]],
        [["set", ["records", 1, "dependencies"], [1]]],
        [["set", ["records", 1, "dependencies"], [{"id": 1}]]],
        [["set", ["records", 0, "dependencies"], [{"namespace": "x", "kind": "rules_reference", "id": SETTING["id"],
                                                   "version": SETTING["version"]}]]],
        [["set", ["records", 0, "id"], ""]],
        [["set", ["records", 0, "files"], []], ["set", ["records", 0, "content_sha256"], "x"]],
        [["delete", ["records", 0, "files"]], ["delete", ["records", 0, "content_sha256"]]],
    ]
    for edits in variants:
        cases.append(run(edits))
    keys = [(index, key) for index, record in enumerate((RULESET, SETTING)) for key in record]
    junk = [None, 1, "x", "", [], {}, "1.0.0", rid, "ruleset", "primary"]
    for _ in range(160):
        index, key = rng.choice(keys)
        if rng.random() < 0.3:
            cases.append(run([["delete", ["records", index, key]]]))
        else:
            cases.append(run([["set", ["records", index, key], rng.choice(junk)]]))
    return cases


def snapshot_cases():
    cases = []
    primary = manifest_from_record(RULESET, format_version=FORMAT)
    setting = manifest_from_record(SETTING, format_version=FORMAT)
    legacy = {key: value for key, value in SETTING.items() if key != "content_kind"}
    legacy["dependencies"] = [{**SETTING["dependencies"][0], "content_sha256": "2" * 64},
                              {"namespace": "other", "kind": "rules_reference", "id": SETTING["id"],
                               "version": SETTING["version"]}]
    legacy_primary = {key: value for key, value in RULESET.items() if key != "content_kind"}
    support = {**legacy_primary, "id": "pneuma.support"}
    for record in (RULESET, SETTING, legacy, legacy_primary, support):
        cases.append({"record": record, "outcome": outcome(lambda: snapshot_record(record, primary=primary,
                                                                                    settings=(setting,)),
                                                           lambda value: value)})
    return cases


def main() -> None:
    vectors = {"python": sys.version.split()[0], "zlib_runtime": zlib.ZLIB_RUNTIME_VERSION,
               "bundled": bundled_facts(), "materialize": materialize_cases(), "snapshot": snapshot_cases(),
               "riichi": str(RIICHI.relative_to(ROOT)).replace(chr(92), "/")}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8",
                      newline="\n")
    kinds = {}
    for case in vectors["materialize"]:
        kinds[case["outcome"][0]] = kinds.get(case["outcome"][0], 0) + 1
    print("srd", vectors["bundled"]["srd_archive"][:16], "rift", vectors["bundled"]["rift_archive"][:16],
          "materialize", kinds, "zlib", zlib.ZLIB_RUNTIME_VERSION)


if __name__ == "__main__":
    main()
