"""A shared table projects only permitted pieces, including after owner restart."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.decision_scope import player_viewer_ids
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.application.piece_table import PieceTableError, project_piece_table
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, StaleRevision

FIXTURE = Path(__file__).parent / "fixtures/piece-table/signal-console.json"


def table():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_each_viewer_gets_only_their_own_pieces_and_public_table():
    raw = table()
    original = deepcopy(raw)
    for viewer, visible, hidden in (
        ("operator", "Ghost route", "Private intercept"),
        ("remote", "Private intercept", "Ghost route"),
    ):
        projected = project_piece_table(raw, viewer_ids={viewer}, action_ids={"run"})
        serialized = json.dumps(thaw_json_value(projected))
        assert visible in serialized
        assert hidden not in serialized
        assert "Online" in serialized
        assert '"count": 1' in serialized
        assert '"owner"' not in serialized
    assert raw == original


def test_concealed_replacement_cannot_leak_piece_ids_labels_or_actions():
    raw = table()
    first = project_piece_table(raw, viewer_ids={"operator"}, action_ids={"run"})
    raw["areas"][2]["groups"][0]["pieces"] = [{"private": "different secret"}]
    assert project_piece_table(raw, viewer_ids={"operator"}, action_ids={"run"}) == first
    raw["areas"][2]["groups"][0]["show_count"] = False
    concealed = project_piece_table(raw, viewer_ids={"operator"}, action_ids={"run"})
    assert "count" not in concealed["areas"][2]["groups"][0]


@pytest.mark.parametrize("problem", [
    "format", "visibility", "action", "fields", "size", "position-type", "unicode",
])
def test_invalid_public_table_fails_without_reflecting_source_data(problem):
    raw = table()
    if problem == "format":
        raw["format"] = True
    elif problem == "visibility":
        raw["areas"][0]["groups"][0]["visibility"] = "guess"
    elif problem == "action":
        raw["areas"][0]["groups"][0]["pieces"][0]["action_id"] = "private-secret-action"
    elif problem == "fields":
        raw["private-secret"] = "never reflect this"
    elif problem == "position-type":
        raw["areas"][0]["position"] = []
    elif problem == "unicode":
        raw["title"] = "\ud800"
    else:
        raw["areas"] *= 17
    with pytest.raises(PieceTableError, match="^Invalid piece table$"):
        project_piece_table(raw, action_ids={"run"})


def test_viewing_identity_is_stable_during_other_actor_turns():
    snapshot = SimpleNamespace(participants=(
        SimpleNamespace(participant_id="operator", data={"control": "player"}),
        SimpleNamespace(participant_id="remote", data={"control": "gm"}),
    ))
    declaration = {"participant_control": {"field": "control", "player_values": ["player"]}}
    assert player_viewer_ids(snapshot, declaration) == {"operator"}
    assert player_viewer_ids(snapshot, None) == set()


@pytest.mark.parametrize("source_kind", ["state", "literal"])
@pytest.mark.parametrize("bind_piece", [False, True])
def test_declared_table_reaches_ordinary_owner_and_reopens_without_hidden_pieces(
    tmp_path, source_kind, bind_piece,
):
    raw = table()
    raw["areas"][0]["groups"][0]["pieces"][0]["action_id"] = "run"
    source = {
        "format": 1, "id": "example.piece-table", "version": "1.0.0",
        "label": "Piece table", "publisher": "Example author", "mechanic_families": {
            "activate": {"parameters": [], "pre_conditions": [], "narrative": {},
                         "effects": [{"set": {"field": "state.used", "value": True}}]},
        },
        "public_actions": [{
            "name": "activate", "family": "activate", "description": "Activate the relay once.",
            "configure": {}, "capability": "example.piece-table.presentation",
            "state_predicates": [{"namespace": "example.piece-table", "key": "state.used",
                                  "equals": False, "negated": False}],
            "turn_statuses": ["open"], "writers": ["dm"],
            "reference_availability": "included", "gameplay_resolution": "engine_family",
            "deterministic_part": None, "gm_part": None,
        }], "private_mechanics": [], "definitions": {},
        "participant_artifacts": [], "operating_guide": None,
        "ui_surfaces": [{
            "id": "console", "capability": "example.piece-table.presentation",
            "actions": [{"id": "run", "label": "Activate relay", "intent": "activate",
                         "params": {}}],
            "renderer": "piece_table", "source": {
                "kind": "state", "namespace": "example.piece-table", "key": "table",
            } if source_kind == "state" else {"kind": "literal", "value": raw},
        }],
    }
    if bind_piece:
        source["mechanic_families"]["activate"]["parameters"] = [{
            "name": "selected_piece", "type": "string", "source": "choice", "required": True,
        }]
        source["mechanic_families"]["activate"]["effects"].append({
            "set": {"field": "state.selected_piece", "value": "selected_piece"},
        })
        source["ui_surfaces"][0]["actions"][0]["bindings"] = {"selected_piece": "piece"}
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode("utf-8"))
    database = tmp_path / "table.db"
    with AuthorityOwner.open(database, "seed") as owner:
        with materialize_package_archive((compiled.draft,)) as artifact:
            candidate = owner.install_package_archive(artifact.archive, install_id="table")
        owner.create_campaign(CampaignCreate(
            campaign_id="table-campaign", display_name="Table", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="turn",
            initial_state=(StateValue("example.piece-table", "table", raw),
                           StateValue("example.piece-table", "state", {"used": False})),
        ))
        before = owner.campaign_panels("table-campaign")
        request = {"campaign_id": "table-campaign", "surface_id": "console",
                   "panel_action_id": "run", "operation_id": "activate-once",
                   "expected_revision": before["revision"]}
        if bind_piece:
            for index, selection in enumerate((
                None, {"piece": "hidden-intercept"}, {"piece": "relay-b"},
                {"piece": "unknown"}, {"piece": "relay-a", "replacement": "extra"},
            )):
                with pytest.raises(CommandRejected, match="selection is invalid"):
                    owner.execute_panel_action(**{
                        **request, "operation_id": f"invalid-{index}", "selection": selection,
                    })
            request["selection"] = {"piece": "relay-a"}
        receipt = owner.execute_panel_action(**request)
        assert receipt.status.value == "committed"
        assert owner.execute_panel_action(**request) == receipt
        with pytest.raises(StaleRevision):
            owner.execute_panel_action(**{**request, "operation_id": "stale"})
        with pytest.raises(CommandRejected, match="unavailable"):
            owner.execute_panel_action(**{
                **request, "operation_id": "withdrawn",
                "expected_revision": receipt.resulting_revision,
            })
        first = thaw_json_value(owner.campaign_panels("table-campaign"))
    with AuthorityOwner.open(database, "reopen") as owner:
        reopened = thaw_json_value(owner.campaign_panels("table-campaign"))
    assert reopened == first
    surface = next(item for item in reopened["surfaces"] if item["id"] == "console")
    assert surface["data"]["title"] == "Signal console"
    assert "source" not in surface
    assert surface["actions"][0]["available"] is False
    assert "Ghost route" not in json.dumps(surface)
    assert "Private intercept" not in json.dumps(surface)
