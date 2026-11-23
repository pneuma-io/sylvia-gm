"""Generate reference vectors for sylvia_app::bundled_campaign_plans and campaign_plan_sources.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_plan_catalogue_vectors.py

Sections:
- `collection`: the repository's bundled Campaign Plan collection: `read_plan_catalogue`,
  `bundled_plan_files`, `bundled_plan` for every catalogued Plan, the retained Rift Plan and an
  unknown id, `bundled_plan_catalog`, `bundled_setting_snapshot`, `known_builtin_public_summary`
  (exact, rebound and edited copies) and `selected_campaign_plan`.
- `synthetic`: small collections written to a scratch root (copies of one real Plan) with
  catalogue and character-path faults, through `read_plan_catalogue` and `bundled_plan_files`.
- `sources`: `campaign_plan_sources` and `validate_retained_campaign_plan` through Python's
  `AuthorityOwner` on lane 3's package-read base (the Riichi profile), and the bundled Rift
  fallback for SRD Lite Plans.
Machine paths are masked as <ROOT>; archives are their SHA-256.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application import bundled_campaign_plans as bcp  # noqa: E402
from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.application.campaign_plan_sources import (  # noqa: E402
    campaign_plan_sources,
    validate_retained_campaign_plan,
)
from sylvia_gm.domain.authority import PackageRole, thaw_json_value  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes, campaign_setting_entries  # noqa: E402

OUTPUT = Path(__file__).with_name("plan_catalogue_vectors.json")
BASE = "crates/sylvia-store/tests/vectors/package_reads/base.sqlite3.gz"
B, NL = chr(92), chr(10)


def digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def masker(*roots):
    def mask(text):
        for root in roots:
            for form in (str(root).replace(B, B + B), str(root), str(root).replace(B, "/")):
                text = text.replace(form, "<ROOT>")
        parts = text.split("<ROOT>")
        for index in range(1, len(parts)):
            path, quote, rest = parts[index].partition("'")
            parts[index] = path.replace(B + B, "/").replace(B, "/") + quote + rest
        return "<ROOT>".join(parts)
    return mask


def outcome(call, shape, mask=lambda text: text):
    try:
        return ["ok", shape(call())]
    except Exception as error:  # every class is behaviour
        diagnostics = getattr(error, "diagnostics", None) or ()
        return [type(error).__name__, mask(str(error)), getattr(error, "code", None),
                [item.as_payload() for item in diagnostics]]


def validated_shape(plan):
    return {"content_sha256": plan.content_sha256, "artifact_id": plan.artifact_id, "title": plan.title,
            "markdown_sha256": digest(plan.readable_markdown.encode("utf-8"))}


def plan_shape(plan):
    if plan is None:
        return None
    return {"artifact_id": plan.artifact_id, "title": plan.title, "summary": plan.summary,
            "content_sha256": plan.content_sha256, "plan": validated_shape(plan.plan),
            "interview_contribution": thaw_json_value(plan.interview_contribution), "setup_id": plan.setup_id,
            "snapshot_sha256": digest(plan.package_snapshot), "catalog": thaw_json_value(plan.catalog_payload())}


def collection_cases(storage):
    root = bcp.bundled_plans_root()
    mask = masker(ROOT, storage)
    relative = lambda paths: [path.relative_to(root).as_posix() for path in paths]  # noqa: E731
    cases = {"catalogue": outcome(lambda: bcp.read_plan_catalogue(root), thaw_json_value),
             "files": outcome(lambda: bcp.bundled_plan_files(root), relative)}
    ids = [row["id"] for row in bcp.read_plan_catalogue(root)] + ["the-rift-awakening", "no-such-plan"]
    cases["plans"] = [[plan_id, outcome(lambda: bcp.bundled_plan(plan_id), plan_shape)] for plan_id in ids]
    cases["catalog"] = outcome(bcp.bundled_plan_catalog, thaw_json_value)
    pairs = [("sylvia.srd-lite.bg3", "pneuma.severed-lands"), ("pneuma.riichi-mahjong", "pneuma.riichi-club"),
             ("sylvia.srd-lite.bg3", "pneuma.riichi-club")]
    cases["settings"] = [[ruleset, setting, outcome(lambda: bcp.bundled_setting_snapshot(ruleset, setting), digest)]
                         for ruleset, setting in pairs]
    summaries = []
    for plan_id in ids[:4] + ["the-rift-awakening"]:
        plan = bcp.bundled_plan(plan_id)
        if plan is None:
            continue
        validated = plan.plan
        summaries.append([plan_id, "exact", outcome(lambda: bcp.known_builtin_public_summary(validated), lambda x: x)])
        for change in ("bindings", "title", "markdown", "id"):
            payload = thaw_json_value(validated.payload)
            markdown = validated.readable_markdown
            if change == "bindings":
                payload["bindings"]["package_closure_sha256"] = "0" * 64
            elif change == "title":
                payload["title"] = payload["title"] + " II"
            elif change == "markdown":
                markdown = markdown + NL
            else:
                payload["id"] = "no-such-plan"
            edited = type(validated)(payload=payload, content_sha256=validated.content_sha256,
                                     artifact_id=validated.artifact_id, title=validated.title,
                                     readable_markdown=markdown,
                                     referenced_setting_paths=validated.referenced_setting_paths)
            summaries.append([plan_id, change, outcome(lambda: bcp.known_builtin_public_summary(edited), lambda x: x)])
    cases["summaries"] = summaries
    cases["selected"] = [[plan_id, outcome(lambda: bcp.selected_campaign_plan(storage, plan_id), plan_shape, mask)]
                         for plan_id in ("the-rift-awakening", ids[0], "campaign-plan-reusable-" + "0" * 24 + "-" + "0" * 16,
                                         "unknown")]
    return cases


def synthetic_cases(scratch):
    real = bcp.bundled_plans_root()
    source_id = "a-debt-in-iron"
    base_row = next(row for row in bcp.read_plan_catalogue(real) if row["id"] == source_id)
    variants = {
        "plain": [dict(base_row)],
        "not_list": {"id": source_id},
        "empty": [],
        "too_many": [dict(base_row, id=f"plan-{index}") for index in range(257)],
        "bad_id": [dict(base_row, id="Bad_Id")],
        "dash_id": [dict(base_row, id="a--b")],
        "duplicate": [dict(base_row), dict(base_row)],
        "no_title": [{key: value for key, value in base_row.items() if key != "title"}],
        "blank_pitch": [dict(base_row, pitch="   ")],
        "number_format": [dict(base_row, format=5)],
    }
    path_faults = {"escape": "../x.json", "absolute": "/x.json", "backslash": "a" + B + "b.json"}
    cases = []
    for name, catalogue in list(variants.items()) + [(f"path_{key}", [dict(base_row)]) for key in path_faults]:
        root = scratch / name
        root.mkdir()
        (root / "catalogue.json").write_text(json.dumps(catalogue), encoding="utf-8")
        shutil.copytree(real / source_id, root / source_id)
        if name.startswith("path_"):
            plan = json.loads((root / source_id / "plan.json").read_text(encoding="utf-8"))
            plan["starting_party"]["player_character"]["character"]["path"] = path_faults[name[5:]]
            (root / source_id / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        mask = masker(root, scratch)
        relative = lambda paths, root=root: [path.relative_to(root).as_posix() for path in paths]  # noqa: E731
        cases.append({"name": name, "catalogue": catalogue,
                      "read": outcome(lambda: bcp.read_plan_catalogue(root), lambda rows: len(rows), mask),
                      "files": outcome(lambda: bcp.bundled_plan_files(root), relative, mask)})
    missing = scratch / "missing_markdown"
    missing.mkdir()
    (missing / "catalogue.json").write_text(json.dumps([base_row]), encoding="utf-8")
    shutil.copytree(real / source_id, missing / source_id)
    (missing / source_id / "plan.md").unlink()
    cases.append({"name": "missing_markdown", "catalogue": [base_row],
                  "read": outcome(lambda: bcp.read_plan_catalogue(missing), lambda rows: len(rows)),
                  "files": outcome(lambda: bcp.bundled_plan_files(missing), lambda paths: len(paths),
                                   masker(missing, scratch))})
    return cases, source_id


def sources_shape(sources):
    if sources is None:
        return None
    return {"ruleset": sources.ruleset.as_payload(), "setting": sources.setting.as_payload(),
            "closure_sha256": sources.closure_sha256, "setting_paths": sorted(sources.setting_paths),
            "setting_knowledge_ids": sorted(sources.setting_knowledge_ids),
            "participant_schemas_sha256": digest(canonical_json_bytes(list(sources.participant_schemas))),
            "setting_character_schema": thaw_json_value(sources.setting_character_schema),
            "state_schemas_sha256": digest(canonical_json_bytes(list(sources.state_schemas))),
            "declarations": [[item.kind, item.schema_id] for item in sources.character_declarations]}


def source_cases(scratch):
    database = scratch / "authority.sqlite3"
    database.write_bytes(gzip.decompress((ROOT / BASE).read_bytes()))
    riichi_plan = json.loads((ROOT / "campaign-plans/riichi-quick-test/plan.json").read_text(encoding="utf-8"))
    riichi_markdown = (ROOT / "campaign-plans/riichi-quick-test/plan.md").read_text(encoding="utf-8")
    cases = []
    with AuthorityOwner.open(database, "plan-catalogue-vectors") as owner:
        candidates = owner.package_candidates()
        for candidate in candidates:
            for setting_id in (None, "pneuma.riichi-club", "pneuma.missing"):
                case = {"candidate": candidate.candidate_id, "setting_id": setting_id,
                        "sources": outcome(lambda: campaign_plan_sources(owner, candidate.package_lock,
                                                                         setting_id=setting_id), sources_shape)}
                try:
                    sources = campaign_plan_sources(owner, candidate.package_lock, setting_id=setting_id)
                except Exception:  # noqa: BLE001 - recorded above
                    sources = None
                if sources is not None:
                    paths = sorted(sources.setting_paths)
                    case["reads"] = [[path, outcome(lambda: sources.read_setting(path), lambda text: digest(text.encode()))]
                                     for path in paths[:3] + ["nowhere.txt"]]
                    rebound = copy.deepcopy(riichi_plan)
                    rebound["bindings"] = {"ruleset": sources.ruleset.as_payload(),
                                           "campaign_setting": sources.setting.as_payload(),
                                           "package_closure_sha256": sources.closure_sha256}
                    case["rebound"] = rebound["bindings"]
                    case["validate"] = [outcome(lambda: sources.validate(riichi_plan, riichi_markdown), validated_shape),
                                        outcome(lambda: sources.validate(rebound, riichi_markdown), validated_shape)]
                cases.append(case)
        retained = []
        plans = {"riichi": (riichi_plan, riichi_markdown)}
        for name in ("a-debt-in-iron", "the-rift-awakening"):
            plans[name] = (json.loads((ROOT / f"campaign-plans/{name}/plan.json").read_text(encoding="utf-8")),
                           (ROOT / f"campaign-plans/{name}/plan.md").read_text(encoding="utf-8"))
        installed = next(item for item in candidates if campaign_setting_entries(item.package_lock))
        primary = next(e for e in installed.package_lock.entries if e.role is PackageRole.PRIMARY)
        setting = campaign_setting_entries(installed.package_lock)[0]
        rebound = copy.deepcopy(riichi_plan)
        rebound["bindings"] = {"ruleset": {"id": primary.package_id, "version": primary.version,
                                           "content_sha256": primary.sha256},
                               "campaign_setting": {"id": setting.package_id, "version": setting.version,
                                                    "content_sha256": setting.sha256},
                               "package_closure_sha256": installed.closure_sha256}
        plans["riichi_rebound"] = (rebound, riichi_markdown)
        current = copy.deepcopy(plans["a-debt-in-iron"][0])
        plans["unknown_closure"] = ({**current, "bindings": {**current["bindings"], "package_closure_sha256": "0" * 64}},
                                    plans["a-debt-in-iron"][1])
        plans["no_bindings"] = ({key: value for key, value in current.items() if key != "bindings"}, plans["a-debt-in-iron"][1])
        plans["setting_list"] = ({**current, "bindings": {**current["bindings"], "campaign_setting": []}},
                                 plans["a-debt-in-iron"][1])
        plans["not_object"] = ([], "")
        for name, (payload, markdown) in plans.items():
            for explicit in (False, True):
                retained.append([name, explicit, outcome(lambda: validate_retained_campaign_plan(
                    owner, payload, markdown, candidates=() if explicit else None), validated_shape)])
    return cases, {name: payload for name, (payload, _) in plans.items() if name not in {"riichi", "a-debt-in-iron",
                                                                                          "the-rift-awakening"}}, retained


def main() -> None:
    with tempfile.TemporaryDirectory() as scratch_name:
        scratch = Path(scratch_name)
        storage = scratch / "storage"
        storage.mkdir()
        collection = collection_cases(storage)
        synthetic_root = scratch / "collections"
        synthetic_root.mkdir()
        synthetic, synthetic_source = synthetic_cases(synthetic_root)
        sources, edited_plans, retained = source_cases(scratch)
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "base": BASE,
               "collection": collection, "synthetic_source": synthetic_source, "synthetic": synthetic,
               "sources": sources, "edited_plans": edited_plans, "retained": retained}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL
    assert str(ROOT) not in text and str(ROOT).replace(B, "/") not in text
    OUTPUT.write_text(text, encoding="utf-8", newline=NL)
    print(len(text), "bytes;", len(collection["plans"]), "plans,", len(synthetic), "synthetic,", len(sources),
          "source cases,", len(retained), "retained")
    for name, explicit, result in retained:
        print("  retained", name, explicit, result[0], ascii(result[1][:70]) if result[0] != "ok" else "")
    for case in synthetic:
        print("  synthetic", case["name"], case["read"][0], case["files"][0], ascii(str(case["files"][1])[:60]))


if __name__ == "__main__":
    main()
