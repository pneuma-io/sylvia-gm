"""Beast form core data stays bound to the admitted SRD source."""

import json
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_beast_forms import build_index

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_ability_checks as checks
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_creature_preparation import _prepare
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case

ROOT = Path(__file__).parents[1] / "rulesets" / "dnd-srd-5.2.1" / "content"
case = checks.case


def test_beast_core_index_covers_and_links_every_admitted_form():
    index = json.loads((ROOT / "beast-form-index.json").read_text(encoding="utf-8"))
    corpus = json.loads((ROOT / "corpus/animals.json").read_text(encoding="utf-8"))
    assert index == build_index()
    assert index["source_sha256"] == corpus["source_sha256"]
    assert index["form_count"] == len(index["forms"]) == 90
    page_hashes = {page["page"]: page["text_sha256"] for page in corpus["page_records"]}
    for form in index["forms"].values():
        assert form["source"]["page_text_sha256"] == page_hashes[form["source"]["page"]]
        assert form["hit_points"] > 0
        assert len(form["ability_scores"]) == len(form["saving_throw_modifiers"]) == 6
    runtime = yaml.safe_load(
        (ROOT.parent / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    )
    assert runtime["definitions"]["beast_forms"] == index["forms"]


def test_beast_core_index_preserves_fractional_cr_and_conditional_speed():
    forms = build_index()["forms"]
    assert forms["ape"]["challenge_rating_eighths"] == 4
    assert forms["ape"]["other_speeds"] == {"climb": 30}
    assert forms["tyrannosaurus_rex"]["challenge_rating_eighths"] == 64
    assert forms["swarm_of_insects"]["other_speeds"] == {}
    assert "Climb or Fly" in forms["swarm_of_insects"]["speed_text"]


def test_transformation_form_limit_uses_source_cr_and_authoritative_target_level():
    state, data, _ = _spell_case("death_ward")
    character = _run(
        "validate_srd_beast_form_for_target", state, participant_data=data,
        form_id="tyrannosaurus_rex", target_state="hero", target_character="hero",
    ).result
    assert character["context"]["_target_rating_eighths"] == 136
    assert character["context"]["_beast_form_profile"]["hit_points"] == 136

    state[f"{SRD}.entities"]["guard"]["challenge_rating_eighths"] = 1
    _run("validate_srd_beast_form_for_target", state,
         form_id="giant_rat", target_state="guard")
    with pytest.raises(RulesetPreconditionRejected, match="exceeds"):
        _run("validate_srd_beast_form_for_target", state,
             form_id="ape", target_state="guard")
    state[f"{SRD}.entities"]["guard"].pop("challenge_rating_eighths")
    with pytest.raises(RulesetPreconditionRejected, match="saved Challenge Rating"):
        _run("validate_srd_beast_form_for_target", state,
             form_id="giant_rat", target_state="guard")


def test_public_creature_preparation_saves_rating_for_form_eligibility(case):
    _prepare(case, challenge_rating_eighths=1)
    saved = case.snapshot("checks")
    assert saved.state[f"{checks.PACKAGE_ID}.entities"]["dera"]["challenge_rating_eighths"] == 1
