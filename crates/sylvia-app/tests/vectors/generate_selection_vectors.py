"""Generate reference vectors for sylvia_app::campaign_library_selection's selection half.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_selection_vectors.py

The owner is builder_references' fake (`generate_builder_reference_vectors.Owner`: candidates
from real admitted archives, recorded installs, archive-backed package reads) plus the two reads
the selection adds: `exact_participant_declarations` (from the admitted manifests) and
`export_package_candidate` (the candidate's archive bytes).

The archives are the bundled Plans' own snapshots (`last-cart-through`, `a-debt-in-iron`, the
retained Rift Plan's), the current bundled Rift archive and Riichi 0.1.1. The storage root holds
a saved Rift sibling without a snapshot and two saved Characters.

Records:
- `resolve`: `resolve_campaign_plan_selection` and `resolve_campaign_library_selection` over
  owner configurations, candidate ids (exact, logical, unknown, broken, archived), Settings,
  Plans and required setups. A success records the candidate, the sources, the Plan, the
  participants, the character state location, the required characters, the party template's
  SHA-256 and `party_options()`; the installs are recorded either way;
- `complete`: `require_completed_plan_characters`;
- `intent`: `plan_with_interview_intent` over intents.
Owner variants change one read: `none` (no participant declarations), `foreign` (Riichi's
declarations for every package), `refuse` (the declarations read raises `CommandRejected`) and
`broken_runtime` (every `runtime/ruleset.yaml` reads as text that is not YAML).
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(HERE)]

import generate_builder_reference_vectors as references  # noqa: E402
from sylvia_gm.application import bundled_campaign_plans as bcp  # noqa: E402
from sylvia_gm.application import bundled_packages as bundled  # noqa: E402
from sylvia_gm.application import bundled_starter_characters as starters  # noqa: E402
from sylvia_gm.application import campaign_library_selection as selection  # noqa: E402
from sylvia_gm.application.reusable_participants import save_reusable_participant  # noqa: E402
from sylvia_gm.application.reusable_plans import save_reusable_rift_plan  # noqa: E402
from sylvia_gm.domain.authority import PackageRole  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402
from sylvia_gm.infrastructure.package_archive import admit_package_archive  # noqa: E402

OUTPUT = HERE / "selection_vectors.json"
NL = chr(10)
B = chr(92)
RIICHI = "rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package"
SRD = "sylvia.srd-lite.bg3"
LANDS = "pneuma.severed-lands"


def thaw(value):
    return json.loads(canonical_json_bytes(value))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def mask(text, roots):
    for root in roots:
        for form in (str(root).replace(B, B + B), str(root), str(root).replace(B, "/")):
            text = text.replace(form, "<ROOT>")
    parts = text.split("<ROOT>")
    for index in range(1, len(parts)):
        path, quote, rest = parts[index].partition("'")
        parts[index] = path.replace(B + B, "/").replace(B, "/") + quote + rest
    return "<ROOT>".join(parts)


class Archive(references.Archive):
    def __init__(self, data):
        super().__init__(data)
        import io
        with admit_package_archive(io.BytesIO(data)) as admitted:
            self.declarations = {m.identity: tuple(m.participant_artifacts) for m in admitted.manifests}


class Owner(references.Owner):
    def __init__(self, archives, names, variant=None):
        super().__init__(archives, names)
        self.variant = variant

    def package_text_file(self, package_id, version, sha256, path, *, maximum_bytes=65536):
        if self.variant == "broken_runtime" and path == "runtime/ruleset.yaml":
            return "rules: [unclosed"
        return super().package_text_file(package_id, version, sha256, path, maximum_bytes=maximum_bytes)

    def install_package_archive(self, source, *, install_id):
        data = source.read()
        archive = Archive(data)
        self.archives[install_id] = archive
        self.installs.append([install_id, digest(data)])
        item = references.PackageCandidate(
            candidate_id=f"cand-{install_id}", install_id=install_id,
            status=references.CandidateStatus.VALIDATED, closure_sha256=archive.closure,
            package_lock=archive.lock, validation_report={})
        self.candidates.append(item)
        return item

    def exact_participant_declarations(self, package_id, version, sha256):
        if self.variant == "none":
            return ()
        if self.variant == "refuse":
            raise references.CommandRejected("Participant declarations are unavailable", code="declarations_unavailable")
        if self.variant == "foreign":
            riichi = self.archives["riichi"]
            return next(value for value in riichi.declarations.values() if value)
        for archive in self.archives.values():
            if (package_id, version, sha256) in archive.declarations:
                return archive.declarations[(package_id, version, sha256)]
        raise references.CommandRejected("Installed package is unavailable")

    def export_package_candidate(self, candidate_id, target):
        for item in self.candidates:
            if item.candidate_id == candidate_id:
                name = candidate_id.removeprefix("cand-")
                target.write_bytes(self.archives[name].data)
                return target
        raise references.CommandRejected("Package candidate is unavailable", code="candidate_unavailable")


def clear_caches():
    starters._ROSTERS.clear()
    for module in (bundled, bcp, starters):
        for value in vars(module).values():
            if callable(getattr(value, "cache_clear", None)):
                value.cache_clear()


def selection_shape(result, roots):
    payload = thaw(result.plan.plan.payload)
    return {
        "candidate_id": result.candidate.candidate_id,
        "closure_sha256": result.candidate.closure_sha256,
        "sources": {"ruleset": thaw(result.sources.ruleset.as_payload()),
                    "setting": thaw(result.sources.setting.as_payload()),
                    "closure_sha256": result.sources.closure_sha256},
        "plan": {"artifact_id": result.plan.artifact_id, "setup_id": result.plan.setup_id,
                 "payload_sha256": digest(canonical_json_bytes(payload))},
        "participants": [item.artifact_id for item in result.participants],
        "builtin": [item.builtin_starter for item in result.participants],
        "entity_reference": None if result.entity_reference is None else thaw(result.entity_reference),
        "required": [[item.artifact_id, item.role, item.name, item.content_sha256]
                     for item in result.required_participants],
        "party_template_sha256": None if result.party_template is None
        else digest(canonical_json_bytes(thaw(result.party_template))),
        "party_options": thaw(result.party_options()),
    }


def error_of(error, roots):
    return [type(error).__name__, mask(str(error), roots), getattr(error, "code", None),
            [thaw(item.as_payload()) for item in getattr(error, "diagnostics", ())]]


def main() -> None:
    clear_caches()
    bundled_rift = bundled.bundled_rift_archive()
    archives = {
        "cart": Archive(bcp.bundled_plan("last-cart-through").package_snapshot),
        "debt": Archive(bcp.bundled_plan("a-debt-in-iron").package_snapshot),
        "retained": Archive(bcp.bundled_plan("the-rift-awakening").package_snapshot),
        "current": Archive(bundled_rift),
        "riichi": Archive((ROOT / RIICHI).read_bytes()),
    }
    owners = {"empty": [], "cart": ["cart"], "all": ["cart", "debt", "retained", "current", "riichi"],
              "current": ["current"]}
    cases = []
    with tempfile.TemporaryDirectory() as scratch:
        storage = Path(scratch) / "storage"
        storage.mkdir()
        roots = (Path(scratch), Path(scratch).resolve())
        rift = bundled.bundled_rift_campaign_plan()
        sibling = save_reusable_rift_plan(storage, operation_id="selection-vectors", title="Saved Rift",
                                          source_payload=thaw(rift.payload), source_markdown=rift.readable_markdown)
        authority = bundled.bundled_rift_authority()
        declaration = bundled.bundled_rift_participant_declaration()
        rows = json.loads((ROOT / "src/sylvia_gm/campaign/srd-starters.json").read_text(encoding="utf-8"))
        fighter = next(row for row in rows if row["id"] == "fighter")
        cart_archive = archives["cart"]
        cart_primary = next(entry for entry in cart_archive.lock.entries if entry.role is PackageRole.PRIMARY)
        cart_setting = bcp.bundled_plan("last-cart-through").plan.payload["bindings"]["campaign_setting"]
        from sylvia_gm.domain.campaign_plan import PackageBinding
        saved = []
        for name in ("Saved Hero", "Saved Friend"):
            participant = copy.deepcopy(fighter["participant"])
            participant["name"] = name
            item = save_reusable_participant(
                storage, operation_id=f"selection-{name}", role="player_character" if name == "Saved Hero"
                else "companion", summary=f"{name}.", authored_profile=f"{name} is saved.",
                participant=participant, entity_state=copy.deepcopy(fighter["entity_state"]),
                package=cart_primary, declaration=next(iter(cart_archive.declarations[(cart_primary.package_id, cart_primary.version, cart_primary.sha256)])),
                campaign_setting=PackageBinding(cart_setting["id"], cart_setting["version"],
                                                cart_setting["content_sha256"]))
            saved.append(item.artifact_id)
        cart_closure = cart_archive.closure
        requests = []
        for owner_name in ("all", "empty", "cart", "current"):
            for plan_id, candidate_id, setting_id in (
                ("last-cart-through", "cand-cart", LANDS),
                ("last-cart-through", SRD, LANDS),
                ("a-debt-in-iron", "cand-debt", LANDS),
                ("a-debt-in-iron", SRD, LANDS),
                ("the-rift-awakening", SRD, LANDS),
                ("the-rift-awakening", "cand-retained", LANDS),
                (sibling.artifact_id, SRD, LANDS),
                ("riichi-quick-test", "pneuma.riichi-mahjong", "pneuma.riichi-club"),
            ):
                requests.append([owner_name, plan_id, candidate_id, setting_id, None])
        for request in (
            ["all", "last-cart-through", "cand-broken", LANDS, None],
            ["all", "last-cart-through", "cand-riichi", LANDS, None],
            ["all", "last-cart-through", "wotc.srd.5.2.1", LANDS, None],
            ["all", "last-cart-through", SRD, "pneuma.riichi-club", None],
            ["all", "last-cart-through", "unknown.ruleset", LANDS, None],
            ["all", "no-such-plan", SRD, LANDS, None],
            ["all", "last-cart-through", SRD, LANDS, "the-rift-awakening"],
            ["all", "the-rift-awakening", SRD, LANDS, "the-rift-awakening"],
            ["all", "riichi-quick-test", "pneuma.riichi-mahjong", LANDS, None],
            ["all", "riichi-quick-test", SRD, "pneuma.riichi-club", None],
        ):
            requests.append(request)
        requests = [[*request, None] for request in requests]
        for variant in ("none", "foreign", "refuse", "broken_runtime"):
            for plan_id in ("a-debt-in-iron", "last-cart-through"):
                requests.append(["all", plan_id, SRD, LANDS, None, variant])
        for owner_name, plan_id, candidate_id, setting_id, setup, variant in requests:
            for kind in ("plan", "library"):
                clear_caches()
                owner = Owner(archives, owners[owner_name], variant)
                try:
                    if kind == "plan":
                        result = selection.resolve_campaign_plan_selection(
                            owner, storage, candidate_id=candidate_id, setting_id=setting_id, plan_id=plan_id,
                            required_setup_id=setup)
                    else:
                        if setup is not None:
                            continue
                        result = selection.resolve_campaign_library_selection(
                            owner, storage, candidate_id=candidate_id, setting_id=setting_id, plan_id=plan_id)
                    outcome = ["ok", selection_shape(result, roots)]
                except Exception as error:  # every class is behaviour
                    outcome = error_of(error, roots)
                cases.append({"kind": kind, "owner": owner_name, "variant": variant, "plan_id": plan_id,
                              "candidate_id": candidate_id, "setting_id": setting_id, "setup": setup, "outcome": outcome,
                              "installs": list(owner.installs)})
        complete = []
        for owner_name, candidate_id, plan_id, variant in (
            ("all", "cand-debt", "a-debt-in-iron", None), ("all", "cand-cart", "a-debt-in-iron", None),
            ("all", "cand-cart", "last-cart-through", None), ("all", "cand-riichi", "riichi-quick-test", None),
            ("all", "cand-missing", "last-cart-through", None), ("all", "cand-broken", "last-cart-through", None),
            ("all", "cand-retained", "the-rift-awakening", None),
            ("all", "cand-debt", "a-debt-in-iron", "none"), ("all", "cand-debt", "a-debt-in-iron", "foreign"),
            ("all", "cand-debt", "a-debt-in-iron", "refuse"), ("all", "cand-cart", "last-cart-through", "refuse"),
        ):
            clear_caches()
            owner = Owner(archives, owners[owner_name], variant)
            plan = bcp.bundled_plan(plan_id).plan
            try:
                selection.require_completed_plan_characters(owner, candidate_id, plan)
                outcome = ["ok", None]
            except Exception as error:  # every class is behaviour
                outcome = error_of(error, roots)
            complete.append({"owner": owner_name, "variant": variant, "candidate_id": candidate_id,
                             "plan_id": plan_id, "outcome": outcome})
        intents = [
            {}, {"boundaries": "No spiders."}, {"preferences": "  More puzzles.  ", "specific_requests": "A ferry."},
            {"boundaries": "   "}, {"boundaries": 5}, {"preferences": None}, {"specific_requests": "x" * 131_073},
            {"specific_requests": "x" * 131_072}, {"contributions": []}, {"contributions": "none"},
            {"contributions": {"tone_and_pace": {"answers": {"pace": "slow", "tone": "grim"}},
                               "a_step": {"answers": {}}, "b_step": "skip", "c_step": {"answers": "no"},
                               "d_step": {"other": 1}}},
            {"contributions": {"z_step": {"answers": {"b": 2, "a": "one"}}, "b_step": {"answers": {"x": None}}}},
            {"boundaries": "Keep it kind.", "contributions": {"z_last": {"answers": {"n": 3, "flag": True,
                                                                                    "list": [1, "a"]}}}},
        ]
        intent_cases = []
        for plan_id, candidate_id in (("last-cart-through", "cand-cart"), ("the-rift-awakening", SRD)):
            clear_caches()
            owner = Owner(archives, owners["all"])
            resolved = selection.resolve_campaign_plan_selection(
                owner, storage, candidate_id=candidate_id, setting_id=LANDS, plan_id=plan_id)
            for intent in intents:
                for markdown in (None, "Replaced Markdown."):
                    try:
                        plan = selection.plan_with_interview_intent(resolved, intent, readable_markdown=markdown)
                        payload = thaw(plan.payload)
                        original = thaw(resolved.plan.plan.payload)
                        outcome = ["ok", {
                            "boundaries": payload["boundaries"], "instructions": payload["gm_guidance"]["instructions"],
                            "markdown_sha256": digest(plan.readable_markdown.encode()),
                            "markdown_tail": plan.readable_markdown[len(markdown or resolved.plan.plan.readable_markdown):],
                            "same_otherwise": {key: value for key, value in payload.items()
                                               if key not in ("boundaries", "gm_guidance")}
                            == {key: value for key, value in original.items() if key not in ("boundaries", "gm_guidance")},
                            "content_sha256": plan.content_sha256}]
                    except Exception as error:  # every class is behaviour
                        outcome = error_of(error, roots)
                    intent_cases.append({"plan_id": plan_id, "candidate_id": candidate_id, "intent": intent,
                                         "markdown": markdown, "outcome": outcome})
    clear_caches()
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]),
               "archives": {"cart": "last-cart-through", "debt": "a-debt-in-iron", "retained": "the-rift-awakening",
                            "current": None, "riichi": RIICHI},
               "owners": owners, "sibling": sibling.artifact_id, "saved": saved, "cart_closure": cart_closure,
               "resolve": cases, "complete": complete, "intent": intent_cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL, encoding="utf-8",
                      newline=NL)
    for case in cases:
        outcome = case["outcome"]
        shown = (outcome[1]["candidate_id"], len(outcome[1]["participants"]), len(outcome[1]["required"]),
                 outcome[1]["party_template_sha256"] is not None) if outcome[0] == "ok" else ascii(outcome[:3])[:110]
        print(case["kind"], case["owner"], case["variant"], case["plan_id"][:18], case["candidate_id"][:14], case["setting_id"][-6:],
              case["setup"], shown, len(case["installs"]))
    for case in complete:
        print("complete", case["variant"], case["candidate_id"], case["plan_id"], ascii(case["outcome"][:3])[:120])
    print("intent", [case["outcome"][0] for case in intent_cases])


if __name__ == "__main__":
    main()
