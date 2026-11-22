"""Generate reference vectors for sylvia_app::campaign_rift (Python's `campaign/rift.py`).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_rift_vectors.py

Inputs are recorded by name so the Rust side rebuilds every case:
- `templates`: the shipped party template (`shipped`), an extended one with more archetypes and
  companions (authored, sparse, Setting-referenced, with setting data), and broken shapes;
- `reusable`: `ReusableParticipant`s, both roles, authored and sparse, one a built-in starter;
- the package lock entry and participant declaration are the bundled Rift Ruleset's, and the
  selected Plan is the bundled Rift Plan.
Records:
- `validate`: `validate_rift_template` on every template;
- `bundled`: `bundled_rift_template()`, which returns the caller's copy;
- `public`: `rift_public_template` for templates and reusable sets;
- `materialize`: `materialize_rift_selection` over lanes, notes, namespaces, policies, choices,
  templates, reusable sets and Setting schemas. A success records the participants, the initial
  state (its SHA-256 and keys), the canonical input, the Plan payload's SHA-256 and the Markdown
  appended to the Plan's;
- `handoff`: `validate_rift_party_handoff` on materialized selections with changed rosters.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from sylvia_gm.application import bundled_packages as bundled  # noqa: E402
from sylvia_gm.application.reusable_participants import ReusableParticipant  # noqa: E402
from sylvia_gm.campaign import rift  # noqa: E402
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole, Participant  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402

OUTPUT = Path(__file__).with_name("rift_vectors.json")
NL = chr(10)
NAMESPACE = "0123456789abcdef01234567"
SCHEMA = {"type": "object", "properties": {"home": {"type": "string"}}, "required": ["home"],
          "additionalProperties": False}


def thaw(value):
    return json.loads(canonical_json_bytes(value))


def sha(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def error_of(error):
    return [type(error).__name__, str(error), getattr(error, "code", None)]


def templates():
    shipped = thaw(rift._template())
    archetype = shipped["player_archetypes"][0]

    def person(identity, name, **extra):
        item = copy.deepcopy(archetype)
        item["id"] = identity
        item["label"] = name
        item["summary"] = f"{name}, ready for the road."
        item["participant"]["name"] = name
        item["entity_state"]["name"] = name
        item["name"] = name
        item.pop("source_ref", None)
        item.update(extra)
        return item

    extended = copy.deepcopy(shipped)
    extended["player_archetypes"] += [
        person("veteran", "Veteran", profile_status="authored", authored_profile="A scarred veteran.",
               source_ref={"id": "veteran", "path": "characters/veteran.md"}),
        person("homebody", "Homebody", setting_data={"home": "Thornwall"}),
        person("unfinished", "Unfinished", reusable=True, profile_status="sparse"),
    ]
    extended["companions"] = [
        person("mira", "Mira", source_ref={"id": "mira", "path": "characters/mira.md"}),
        person("tobin", "Tobin", profile_status="authored", authored_profile="Tobin keeps the ledger."),
        person("ash", "Ash", setting_data={"home": "Ashfall"}),
        person("wren", "Wren", source_ref={"id": "wren", "path": "characters/wren.md"}),
    ]
    variants = {"shipped": shipped, "extended": extended}

    def broken(name, edit, base=shipped):
        value = copy.deepcopy(base)
        edit(value)
        variants[name] = value

    broken("format_3", lambda v: v.update(format=3))
    broken("format_float", lambda v: v.update(format=2.0))
    broken("format_true", lambda v: v.update(format=True))
    broken("other_id", lambda v: v.update(template_id="another"))
    broken("extra_key", lambda v: v.update(extra=1))
    broken("missing_title", lambda v: v.pop("title"))
    broken("no_archetypes", lambda v: v.update(player_archetypes=[]))
    broken("archetypes_object", lambda v: v.update(player_archetypes={"a": 1}))
    broken("companions_text", lambda v: v.update(companions="none"))
    broken("six_companions", lambda v: v.update(companions=[person(f"c{i}", f"C{i}") for i in range(6)]))
    broken("archetype_not_object", lambda v: v["player_archetypes"].append("x"))
    broken("archetype_repeated", lambda v: v["player_archetypes"].append(copy.deepcopy(archetype)))
    broken("archetype_id_list", lambda v: v["player_archetypes"].append({**person("z", "Z"), "id": ["z"]}))
    broken("archetype_id_number", lambda v: v["player_archetypes"].append({**person("z", "Z"), "id": 7}))
    broken("archetype_no_id", lambda v: v["player_archetypes"].append(
        {key: value for key, value in person("z", "Z").items() if key != "id"}))
    broken("companion_repeated", lambda v: v["companions"].extend([person("m", "M"), person("m", "M")]))
    broken("not_an_object", lambda v: None)
    variants["not_an_object"] = ["shipped"]
    broken("archetype_no_participant", lambda v: v["player_archetypes"][0].pop("participant"))
    broken("archetype_participant_list", lambda v: v["player_archetypes"][0].update(participant=[]))
    broken("archetype_no_summary", lambda v: v["player_archetypes"][0].pop("summary"))
    broken("companion_no_name", lambda v: v["companions"].append(
        {key: value for key, value in person("n", "N").items() if key != "name"}))
    broken("encounters_list", lambda v: v.update(encounter_entities=[["rift-opening-riftwolf", {"hp": 1}]]))
    broken("encounters_text", lambda v: v.update(encounter_entities="riftwolf"))
    broken("encounters_numbers", lambda v: v.update(encounter_entities=[5]))
    broken("encounters_triple", lambda v: v.update(encounter_entities=[["a", "b", "c"]]))
    broken("encounters_unhashable", lambda v: v.update(encounter_entities=[[["k"], 1]]))
    broken("encounters_null", lambda v: v.update(encounter_entities=None))
    broken("encounters_number", lambda v: v.update(encounter_entities=5))
    broken("encounters_chars", lambda v: v.update(encounter_entities=["ab"]))
    broken("encounters_pair_object", lambda v: v.update(encounter_entities=[{"x": 1, "y": 2}]))
    broken("reusable_one", lambda v: v["player_archetypes"][0].update(reusable=1))
    broken("reusable_true", lambda v: v["player_archetypes"][0].update(reusable=True))
    broken("encounters_empty", lambda v: v.update(encounter_entities={}))
    broken("encounters_override_player", lambda v: v["encounter_entities"].update(
        {f"rift-{NAMESPACE}-player-character": {"hp": 1}}))
    broken("bad_profile_status", lambda v: v["player_archetypes"][0].update(profile_status="draft"))
    broken("profile_status_list", lambda v: v["player_archetypes"][0].update(profile_status=["authored"]))
    broken("authored_without_text", lambda v: v["player_archetypes"][0].update(profile_status="authored"))
    broken("sparse_with_text", lambda v: v["player_archetypes"][0].update(authored_profile="text"))
    broken("participant_bad_schema", lambda v: v["player_archetypes"][0]["participant"].update(level="one"))
    broken("entity_state_stale", lambda v: v["player_archetypes"][0]["entity_state"].update(armor_class=99))
    broken("entity_state_list", lambda v: v["player_archetypes"][0].update(entity_state=[]))
    broken("setting_data_list", lambda v: v["player_archetypes"][0].update(setting_data=[]))
    broken("setting_data_null", lambda v: v["player_archetypes"][0].update(setting_data=None))
    broken("summary_number", lambda v: v["player_archetypes"][0].update(summary=12))
    broken("name_number", lambda v: v["player_archetypes"][0]["participant"].update(name=12), extended)
    return variants


def reusable():
    archetype = thaw(rift._template())["player_archetypes"][0]
    authority = bundled.bundled_rift_authority()
    declaration = bundled.bundled_rift_participant_declaration()

    def item(suffix, role, name, authored, **extra):
        participant = copy.deepcopy(archetype["participant"])
        participant["name"] = name
        fields = {
            "artifact_id": f"participant-{'a' * 24}-{suffix:0>16}", "role": role, "name": name,
            "summary": f"{name} from the library.", "authored_profile": authored,
            "content_sha256": hashlib.sha256(name.encode()).hexdigest(),
            "package_id": authority.ruleset.package_id, "package_version": authority.ruleset.version,
            "package_sha256": authority.ruleset.content_sha256, "schema_id": declaration.schema_id,
            "schema_sha256": declaration.schema_sha256, "campaign_setting_id": authority.setting.package_id,
            "campaign_setting_version": authority.setting.version,
            "campaign_setting_sha256": authority.setting.content_sha256, "participant": participant,
            "entity_state": {**copy.deepcopy(archetype["entity_state"]), "name": name}, "setting_data": {},
            "source_profile": None, "source_migration": None, "builtin_starter": False,
        }
        fields.update(extra)
        return fields

    return {
        "saved_player": item("1", "player_character", "Saved Player", "A saved hero."),
        "sparse_player": item("2", "player_character", "Sparse Player", None),
        "starter": item("3", "player_character", "Starter", "A built-in starter.", builtin_starter=True,
                        source_profile={"id": "srd-fighter", "title": "Fighter"}),
        "saved_companion": item("4", "companion", "Saved Companion", "A loyal friend."),
        "sparse_companion": item("5", "companion", "Sparse Companion", None,
                                 source_profile={"id": "old", "title": "Old"}),
        "homed_companion": item("6", "companion", "Homed Companion", "From the harbor.",
                                setting_data={"home": "Glass Harbor"}),
        "clashing_player": item("7", "player_character", "Clash", "Clashes.", artifact_id="open-adventurer"),
    }


def participants_of(names, library):
    return tuple(ReusableParticipant(**copy.deepcopy(library[name])) for name in names)


def participant_payload(participant: Participant):
    return {"participant_id": participant.participant_id, "kind": participant.kind,
            "package_id": participant.package_id, "package_version": participant.package_version,
            "data": thaw(participant.data), "package_sha256": participant.package_sha256,
            "schema_id": participant.schema_id, "schema_sha256": participant.schema_sha256,
            "validation_status": participant.validation_status.value}


def selection_payload(selection, plan_markdown):
    state = [[value.namespace, value.key, thaw(value.value)] for value in selection.initial_state]
    markdown = selection.campaign_plan_markdown
    assert markdown.startswith(plan_markdown)
    return {"participants": [participant_payload(item) for item in selection.participants],
            "state_keys": [row[:2] for row in state], "state_sha256": sha(state),
            "canonical_input": thaw(selection.canonical_input), "plan_sha256": sha(selection.campaign_plan),
            "markdown_tail": markdown[len(plan_markdown):]}


def policy(mode="compatible", fixed=None, required=(), minimum=0, maximum=5):
    player = {"mode": mode}
    if fixed is not None:
        player["character"] = {"id": fixed[0], "path": fixed[1]}
    return {"player_character": player,
            "required_companions": [{"id": identity, "path": path} for identity, path in required],
            "custom_companions": {"minimum": minimum, "maximum": maximum}}


MIRA = ("mira", "characters/mira.md")
WREN = ("wren", "characters/wren.md")
VETERAN = ("veteran", "characters/veteran.md")


def materialize_cases():
    base = {"namespace": NAMESPACE, "lane": "quick_start", "archetype": "open-adventurer", "companions": [],
            "notes": "", "policy": None, "template": None, "reusable": [], "plan": False, "schema": None}
    changes = [
        {},
        {"template": "shipped"},
        {"plan": True},
        {"namespace": "0123456789ABCDEF01234567"},
        {"namespace": "0123"},
        {"namespace": "0123456789abcdef0123456z"},
        {"lane": "story"},
        {"lane": "customize", "notes": "  Lean into the mystery.  "},
        {"lane": "customize", "notes": "   "},
        {"lane": "quick_start", "notes": "Rewrite it"},
        {"lane": "customize", "notes": "x" * 4001},
        {"lane": "customize", "notes": "x" * 4000},
        {"archetype": "nobody"},
        {"companions": ["a", "b", "c", "d", "e", "f"]},
        {"companions": ["mira", "mira"]},
        {"template": "extended", "companions": ["mira", "tobin"]},
        {"template": "extended", "companions": ["tobin", "mira", "ash", "wren"], "schema": SCHEMA},
        {"template": "extended", "companions": ["ash"]},
        {"template": "extended", "companions": ["ash"], "schema": SCHEMA},
        {"template": "extended", "archetype": "homebody"},
        {"template": "extended", "archetype": "homebody", "schema": SCHEMA},
        {"template": "extended", "archetype": "veteran", "companions": ["nobody"]},
        {"template": "extended", "archetype": "unfinished"},
        {"template": "extended", "archetype": "veteran",
         "policy": policy("fixed", fixed=VETERAN, required=[MIRA], minimum=0, maximum=2),
         "companions": ["mira", "tobin"]},
        {"template": "extended", "archetype": "open-adventurer",
         "policy": policy("fixed", fixed=VETERAN)},
        {"template": "extended", "archetype": "veteran",
         "policy": policy("fixed", fixed=("veteran", "characters/other.md"))},
        {"template": "extended", "policy": policy(required=[MIRA, WREN], maximum=3), "companions": ["mira"]},
        {"template": "extended", "policy": policy(required=[MIRA, WREN], maximum=3),
         "companions": ["wren", "mira", "tobin"]},
        {"template": "extended", "policy": policy(required=[("mira", "characters/elsewhere.md")]),
         "companions": ["mira"]},
        {"template": "extended", "policy": policy(minimum=1, maximum=2), "companions": []},
        {"template": "extended", "policy": policy(minimum=1, maximum=2), "companions": ["tobin", "ash", "mira"]},
        {"template": "extended", "policy": policy(required=[MIRA, WREN], maximum=4), "companions": ["mira", "wren"]},
        {"policy": {"player_character": {"mode": "secret"}}},
        {"policy": policy(maximum=6)},
        {"reusable": ["saved_player", "saved_companion"], "archetype": "participant-" + "a" * 24 + "-0000000000000001",
         "companions": ["participant-" + "a" * 24 + "-0000000000000004"]},
        {"reusable": ["sparse_player"], "archetype": "participant-" + "a" * 24 + "-0000000000000002"},
        {"reusable": ["sparse_companion"], "companions": ["participant-" + "a" * 24 + "-0000000000000005"]},
        {"reusable": ["starter"], "archetype": "participant-" + "a" * 24 + "-0000000000000003"},
        {"reusable": ["homed_companion"], "companions": ["participant-" + "a" * 24 + "-0000000000000006"]},
        {"reusable": ["homed_companion"], "companions": ["participant-" + "a" * 24 + "-0000000000000006"],
         "schema": SCHEMA},
        {"reusable": ["clashing_player"]},
        {"template": "extended", "reusable": ["saved_player", "saved_companion", "starter"],
         "companions": ["tobin", "participant-" + "a" * 24 + "-0000000000000004"], "lane": "customize",
         "notes": "Short notes."},
    ]
    for name in ("format_3", "not_an_object", "archetype_no_participant", "archetype_participant_list",
                 "archetype_no_summary", "encounters_list", "encounters_text", "encounters_empty",
                 "encounters_override_player", "encounters_numbers", "encounters_triple",
                 "encounters_unhashable", "encounters_null", "encounters_number", "encounters_chars",
                 "encounters_pair_object", "reusable_one", "reusable_true", "bad_profile_status", "profile_status_list",
                 "authored_without_text", "sparse_with_text", "participant_bad_schema", "entity_state_stale",
                 "entity_state_list", "setting_data_list", "setting_data_null", "summary_number",
                 "archetype_no_id"):
        changes.append({"template": name})
    changes.append({"template": "name_number", "archetype": "homebody"})
    changes.append({"template": "name_number", "archetype": "veteran", "companions": ["tobin"]})
    return [{**base, **change} for change in changes]


def run_materialize(case, variants, library, package, declaration, plan):
    try:
        return ["ok", selection_payload(rift.materialize_rift_selection(
            participant_namespace=case["namespace"], lane=case["lane"], archetype_id=case["archetype"],
            companion_ids=tuple(case["companions"]), customization_notes=case["notes"], package=package,
            participant_declaration=declaration, starting_party_policy=case["policy"],
            reusable_participants=participants_of(case["reusable"], library),
            template=None if case["template"] is None else copy.deepcopy(variants[case["template"]]),
            selected_plan=plan if case["plan"] else None, setting_character_schema=case["schema"],
        ), plan.readable_markdown)]
    except Exception as error:  # every class is behaviour
        return error_of(error)


def handoff_cases(variants, library, package, declaration, plan):
    made = rift.materialize_rift_selection(
        participant_namespace=NAMESPACE, lane="quick_start", archetype_id="open-adventurer",
        companion_ids=("mira", "tobin"), customization_notes="", package=package,
        participant_declaration=declaration, starting_party_policy=policy(required=[MIRA], maximum=4),
        template=copy.deepcopy(variants["extended"]))
    player, mira, tobin = made.participants
    party = thaw(made.canonical_input["selected_party"])

    def selection(participants, selected_party):
        return rift.RiftSelection(participants=tuple(participants), initial_state=(),
                                  canonical_input={"selected_party": selected_party}, campaign_plan={},
                                  campaign_plan_markdown="")

    def recast(participant, role):
        data = dict(participant.data)
        if role is None:
            data.pop("party_role")
        else:
            data["party_role"] = role
        return Participant(participant_id=participant.participant_id, kind=participant.kind,
                           package_id=participant.package_id, package_version=participant.package_version,
                           data=data, package_sha256=participant.package_sha256, schema_id=participant.schema_id,
                           schema_sha256=participant.schema_sha256,
                           validation_status=participant.validation_status)

    unrequired = copy.deepcopy(party)
    unrequired[1].pop("required")
    rows = {
        "as_made": ([player, mira, tobin], party),
        "no_player": ([mira, tobin], party),
        "two_players": ([player, recast(mira, "player_character"), tobin], party),
        "role_missing": ([player, recast(mira, None), tobin], party),
        "required_missing": ([player, mira, tobin], unrequired),
        "required_true_text": ([player, mira, tobin], [*party[:1], {**party[1], "required": "yes"}, party[2]]),
        "party_not_list": ([player, mira, tobin], {"a": 1}),
        "party_missing": ([player, mira, tobin], None),
        "party_items_mixed": ([player, mira, tobin], [*party, "text", 3]),
        "six_companions": ([player, *[mira] * 6], party),
    }
    policies = {"made": policy(required=[MIRA], maximum=4), "none_required": policy(maximum=4),
                "custom_one": policy(required=[MIRA], minimum=2, maximum=4),
                "bad": {"player_character": {"mode": "secret"}}}
    cases = []
    for name, (participants, selected_party) in rows.items():
        for policy_name, value in policies.items():
            canonical = {} if selected_party is None else selected_party
            item = selection(participants, canonical)
            if selected_party is None:
                item = rift.RiftSelection(item.participants, (), {}, {}, "")
            try:
                rift.validate_rift_party_handoff(item, value)
                result = ["ok", None]
            except Exception as error:  # every class is behaviour
                result = error_of(error)
            cases.append({"roster": name, "policy": policy_name,
                          "participants": [participant_payload(p) for p in participants],
                          "selected_party": selected_party, "outcome": result})
    return policies, cases


def main() -> None:
    variants = templates()
    library = reusable()
    authority = bundled.bundled_rift_authority()
    declaration = bundled.bundled_rift_participant_declaration()
    package = PackageLockEntry(authority.ruleset.package_id, authority.ruleset.version,
                               authority.ruleset.content_sha256, PackageRole.PRIMARY, 0, (), ())
    plan = bundled.bundled_rift_campaign_plan()
    validate = {}
    for name, value in variants.items():
        try:
            validate[name] = ["ok", thaw(rift.validate_rift_template(copy.deepcopy(value)))]
        except Exception as error:  # every class is behaviour
            validate[name] = error_of(error)
    first = rift.bundled_rift_template()
    first["title"] = "edited"
    assert rift.bundled_rift_template() != first
    public = []
    for template in (None, "shipped", "extended", "archetype_no_participant", "archetype_no_summary",
                     "companion_no_name", "format_3", "not_an_object", "summary_number"):
        for names in ([], ["saved_player", "starter", "saved_companion", "sparse_companion", "sparse_player"]):
            try:
                result = ["ok", thaw(rift.rift_public_template(
                    reusable_participants=participants_of(names, library),
                    template=None if template is None else copy.deepcopy(variants[template])))]
            except Exception as error:  # every class is behaviour
                result = error_of(error)
            public.append({"template": template, "reusable": names, "outcome": result})
    materialize = [{**case, "outcome": run_materialize(case, variants, library, package, declaration, plan)}
                   for case in materialize_cases()]
    handoff_policies, handoff = handoff_cases(variants, library, package, declaration, plan)
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]),
               "package": {"id": package.package_id, "version": package.version, "sha256": package.sha256},
               "templates": variants, "reusable": library, "validate": validate,
               "bundled": thaw(rift.bundled_rift_template()), "public": public, "materialize": materialize,
               "handoff_policies": handoff_policies, "handoff": handoff}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL, encoding="utf-8",
                      newline=NL)
    for case in materialize:
        print("materialize", case["template"], case["archetype"][-6:], case["companions"][:3], case["lane"],
              ascii(case["outcome"][0]), ascii(case["outcome"][1])[:90] if case["outcome"][0] != "ok" else "")
    for case in public:
        print("public", case["template"], len(case["reusable"]), ascii(case["outcome"])[:90])
    print({name: value[0] for name, value in validate.items()})
    print([(case["roster"], case["policy"], case["outcome"][0]) for case in handoff])


if __name__ == "__main__":
    main()
