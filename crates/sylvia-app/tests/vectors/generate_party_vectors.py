"""Generate reference vectors for sylvia_app::campaign_library_selection's party materialization.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_party_vectors.py

The owner, archives and storage root are the selection vectors' (`generate_selection_vectors`):
the fake owner over real admitted archives, a saved Rift sibling and two saved Characters. Each
case resolves a library selection, then materializes a party from it:
- `library`: `materialize_library_party` for compatible, fixed and required-character Plans
  (Last Cart Through, A Debt in Iron, Riichi), with starters, saved Characters and the policy's
  refusals (counts, repeats, unknown and wrongly cast choices, the fixed Player Character, a
  missing required file);
- `template`: `materialize_template_party` for the Rift party-template setup, with intents.
A success records the participants, each state value's namespace, key and SHA-256, the selected
party and (for a template party) the Plan's payload SHA-256 and the Markdown appended to the
Plan's.
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

import generate_selection_vectors as sel  # noqa: E402
from sylvia_gm.application import bundled_campaign_plans as bcp  # noqa: E402
from sylvia_gm.application import bundled_packages as bundled  # noqa: E402
from sylvia_gm.application import campaign_library_selection as selection  # noqa: E402
from sylvia_gm.application.reusable_participants import save_reusable_participant  # noqa: E402
from sylvia_gm.application.reusable_plans import save_reusable_rift_plan  # noqa: E402
from sylvia_gm.domain.authority import PackageRole  # noqa: E402
from sylvia_gm.domain.campaign_plan import PackageBinding  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402

OUTPUT = HERE / "party_vectors.json"
NL = chr(10)
SRD = "sylvia.srd-lite.bg3"
LANDS = "pneuma.severed-lands"


def thaw(value):
    return json.loads(canonical_json_bytes(value))


def digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def participant_payload(participant):
    return {"participant_id": participant.participant_id, "kind": participant.kind,
            "package_id": participant.package_id, "package_version": participant.package_version,
            "data": thaw(participant.data), "package_sha256": participant.package_sha256,
            "schema_id": participant.schema_id, "schema_sha256": participant.schema_sha256,
            "validation_status": participant.validation_status.value}


def state_payload(state):
    return [[value.namespace, value.key, digest(value.value)] for value in state]


def setup(storage, archives):
    rift = bundled.bundled_rift_campaign_plan()
    sibling = save_reusable_rift_plan(storage, operation_id="selection-vectors", title="Saved Rift",
                                      source_payload=thaw(rift.payload), source_markdown=rift.readable_markdown)
    rows = json.loads((ROOT / "src/sylvia_gm/campaign/srd-starters.json").read_text(encoding="utf-8"))
    fighter = next(row for row in rows if row["id"] == "fighter")
    cart = archives["cart"]
    primary = next(entry for entry in cart.lock.entries if entry.role is PackageRole.PRIMARY)
    declaration = next(iter(cart.declarations[(primary.package_id, primary.version, primary.sha256)]))
    setting = bcp.bundled_plan("last-cart-through").plan.payload["bindings"]["campaign_setting"]
    saved = []
    for name in ("Saved Hero", "Saved Friend"):
        participant = copy.deepcopy(fighter["participant"])
        participant["name"] = name
        item = save_reusable_participant(
            storage, operation_id=f"selection-{name}", role="player_character" if name == "Saved Hero"
            else "companion", summary=f"{name}.", authored_profile=f"{name} is saved.",
            participant=participant, entity_state=copy.deepcopy(fighter["entity_state"]),
            package=primary, declaration=declaration,
            campaign_setting=PackageBinding(setting["id"], setting["version"], setting["content_sha256"]))
        saved.append(item.artifact_id)
    return sibling.artifact_id, saved


def main() -> None:
    sel.clear_caches()
    archives = {
        "cart": sel.Archive(bcp.bundled_plan("last-cart-through").package_snapshot),
        "debt": sel.Archive(bcp.bundled_plan("a-debt-in-iron").package_snapshot),
        "retained": sel.Archive(bcp.bundled_plan("the-rift-awakening").package_snapshot),
        "current": sel.Archive(bundled.bundled_rift_archive()),
        "riichi": sel.Archive((ROOT / sel.RIICHI).read_bytes()),
    }
    names = ["cart", "debt", "retained", "current", "riichi"]
    cases = []
    with tempfile.TemporaryDirectory() as scratch:
        storage = Path(scratch) / "storage"
        storage.mkdir()
        roots = (Path(scratch), Path(scratch).resolve())
        sibling, saved = setup(storage, archives)
        hero, friend = saved

        def resolved(plan_id, candidate_id, setting_id, variant=None):
            sel.clear_caches()
            owner = sel.Owner(archives, names, variant)
            return owner, selection.resolve_campaign_library_selection(
                owner, storage, candidate_id=candidate_id, setting_id=setting_id, plan_id=plan_id)

        sel.clear_caches()
        probe_owner, probe = resolved("last-cart-through", "cand-cart", LANDS)
        starters = [item.artifact_id for item in probe.participants if item.builtin_starter]
        starter = starters[0]
        debt_owner, debt = resolved("a-debt-in-iron", SRD, LANDS)
        debt_pc = debt.required_participants[0].artifact_id
        library = [
            ("last-cart-through", "cand-cart", LANDS, None, starter, []),
            ("last-cart-through", "cand-cart", LANDS, None, hero, [friend]),
            ("last-cart-through", "cand-cart", LANDS, None, starter, [friend, friend]),
            ("last-cart-through", "cand-cart", LANDS, None, starter, [friend, "nobody", "other"]),
            ("last-cart-through", "cand-cart", LANDS, None, "nobody", []),
            ("last-cart-through", "cand-cart", LANDS, None, friend, []),
            ("last-cart-through", "cand-cart", LANDS, None, hero, [hero]),
            ("last-cart-through", "cand-cart", LANDS, None, hero, ["nobody"]),
            ("a-debt-in-iron", SRD, LANDS, None, debt_pc, []),
            ("a-debt-in-iron", SRD, LANDS, None, debt_pc, [friend]),
            ("a-debt-in-iron", SRD, LANDS, None, hero, []),
            ("a-debt-in-iron", SRD, LANDS, "none", debt_pc, []),
            ("riichi-quick-test", "pneuma.riichi-mahjong", "pneuma.riichi-club", None, "you", []),
            ("the-rift-awakening", SRD, LANDS, None, starter, [friend]),
        ]
        for plan_id, candidate_id, setting_id, variant, player, companions in library:
            owner, chosen = resolved(plan_id, candidate_id, setting_id, variant)
            try:
                participants, state, party = selection.materialize_library_party(
                    owner, chosen, operation_id="party-operation", player_character_id=player,
                    companion_ids=tuple(companions))
                outcome = ["ok", {"participants": [participant_payload(p) for p in participants],
                                  "state": state_payload(state), "selected_party": thaw(party)}]
            except Exception as error:  # every class is behaviour
                outcome = sel.error_of(error, roots)
            cases.append({"kind": "library", "plan_id": plan_id, "candidate_id": candidate_id,
                          "setting_id": setting_id, "variant": variant, "player": player,
                          "companions": companions, "intent": None, "outcome": outcome})
        template = [
            ("open-adventurer", [friend], {}),
            ("open-adventurer", [friend], {"boundaries": "No spiders.", "contributions": {
                "tone": {"answers": {"pace": "slow"}}}}),
            (starter, [friend], {}),
            (hero, [friend], {"preferences": "Mysteries."}),
            ("open-adventurer", [], {}),
            ("open-adventurer", [friend, friend], {}),
            ("nobody", [friend], {}),
            ("open-adventurer", ["nobody"], {}),
            ("open-adventurer", [friend], {"boundaries": 5}),
        ]
        for player, companions, intent in template:
            owner, chosen = resolved("the-rift-awakening", SRD, LANDS)
            try:
                participants, state, party, plan = selection.materialize_template_party(
                    owner, chosen, operation_id="party-operation", player_character_id=player,
                    companion_ids=tuple(companions), intent=intent)
                base = chosen.plan.plan.readable_markdown
                outcome = ["ok", {"participants": [participant_payload(p) for p in participants],
                                  "state": state_payload(state), "selected_party": thaw(party),
                                  "plan_sha256": digest(plan.payload),
                                  "markdown_tail": plan.readable_markdown[len(base):]
                                  if plan.readable_markdown.startswith(base) else None,
                                  "content_sha256": plan.content_sha256}]
            except Exception as error:  # every class is behaviour
                outcome = sel.error_of(error, roots)
            cases.append({"kind": "template", "plan_id": "the-rift-awakening", "candidate_id": SRD,
                          "setting_id": LANDS, "variant": None, "player": player, "companions": companions,
                          "intent": intent, "outcome": outcome})
    sel.clear_caches()
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "riichi": sel.RIICHI,
               "sibling": sibling, "saved": saved, "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL, encoding="utf-8",
                      newline=NL)
    for case in cases:
        outcome = case["outcome"]
        shown = (len(outcome[1]["participants"]), len(outcome[1]["state"])) if outcome[0] == "ok" \
            else ascii(outcome[:3])[:130]
        print(case["kind"], case["plan_id"][:16], case["variant"], case["player"][-8:],
              [c[-6:] for c in case["companions"]], shown)


if __name__ == "__main__":
    main()
