"""A campaign's panels through Python's owner: `AuthorityOwner.campaign_panels`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_campaign_panel_runs.py

Five scenarios, each on its own base (`campaign_panels/<scenario>/base.sqlite3.gz`):
- `coordinate`: `tests/test_player_actions.py`'s synthetic coordinate campaign as created (a
  coordinate map with a move action, and the generic inventory panel);
- `response`: the same campaign after a confirmed panel move whose control asks an automatic
  response, so a player response is pending. Its steps add every `after_revision` form (absent,
  0, the current revision, and refused: negative, past the revision, a bool, a string, a float),
  the panels while the GM session's work is held (direct actions unavailable), and a campaign
  that does not exist;
- `network`: `tests/test_network_graph_ui.py`'s network graph campaign, with its actor binding;
- `gated`: `tests/test_coordinate_map_ui.py`'s gated coordinate map (its source carries an `enabled`
  field read from state), in a campaign where it is off (the map is left out) and one where it is on;
- `table`: `tests/test_piece_table.py`'s declared piece table, in a campaign whose saved table is
  valid (projected, its `source` dropped) and one whose saved table is not (the unavailable surface).

Each step records its answer or failure; `final.sqlite3.gz` is each database after its steps.
Reference tooling only.
"""

from __future__ import annotations

import gzip
import io
import json
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

import yaml  # noqa: E402

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate  # noqa: E402
from sylvia_gm.application.package_materialization import materialize_package_archive  # noqa: E402
from sylvia_gm.builder._ruleset_source import compile_ruleset_source  # noqa: E402
from sylvia_gm.domain.authority import StateValue, WriterRole  # noqa: E402
from tests.test_coordinate_map_ui import coordinate_archive  # noqa: E402
from tests.test_network_graph_ui import _archive, _graph  # noqa: E402
from tests.test_piece_table import table  # noqa: E402
from tests.test_player_actions import _create, _move, _request  # noqa: E402
from tests.test_tactical_grid_ui import _scene  # noqa: E402

OUT = HERE / "campaign_panels"
OWNER = "owner"


def ordered(value: object) -> object:
    """A frozen payload as plain JSON, keeping Python's own key order (never sorted)."""
    if isinstance(value, Mapping):
        return {str(key): ordered(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [ordered(item) for item in value]
    return value


def coordinate(owner: AuthorityOwner) -> None:
    _create(owner)


def response(owner: AuthorityOwner) -> None:
    _create(owner, controls={"confirmation": "required", "response": "automatic"})
    proposal = owner.preview_panel_action(**_request())
    _move(owner, confirmation=proposal["confirmation"])


def network(owner: AuthorityOwner) -> None:
    actor_id = "participant-admitted-hex"
    initial = {
        "network": _graph(), "last_actor": None, "last_node": None, "last_function": None,
        "decision_owner": "player", "active_side": "allies", "focused_actor": None,
        "eligible_actor_ids": [actor_id],
        "entities": {actor_id: {"name": "Hex", "ap": 6}, "hostile": {"name": "Hostile"}},
    }
    candidate = owner.install_package_archive(io.BytesIO(_archive(actor_binding=True)), install_id="network-actors")
    owner.create_campaign(CampaignCreate(
        campaign_id="campaign", display_name="Network actor selection", writer=WriterRole.DM,
        package_lock=candidate.package_lock, turn_id="network-turn",
        initial_state=(StateValue("synthetic.graph", "state", cast(object, initial)),),  # type: ignore[arg-type]
    ))


def gated(owner: AuthorityOwner) -> None:
    candidate = owner.install_package_archive(io.BytesIO(coordinate_archive(gated=True)), install_id="gated")
    for campaign_id, active in (("campaign", False), ("open-campaign", True)):
        owner.create_campaign(CampaignCreate(
            campaign_id=campaign_id, display_name="Gated coordinate map", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="turn",
            initial_state=(StateValue("synthetic.coordinate", "state", {
                "spatial_scene": _scene(), "combat_active": active,
                "last_actor": None, "last_destination": None,
            }),),
        ))


def piece_table(owner: AuthorityOwner) -> None:
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
            "actions": [{"id": "run", "label": "Activate relay", "intent": "activate", "params": {}}],
            "renderer": "piece_table",
            "source": {"kind": "state", "namespace": "example.piece-table", "key": "table"},
        }],
    }
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode("utf-8"))
    with materialize_package_archive((compiled.draft,)) as artifact:
        candidate = owner.install_package_archive(artifact.archive, install_id="table")
    broken = table()
    broken["format"] = True
    for campaign_id, saved in (("campaign", raw), ("broken-campaign", broken)):
        owner.create_campaign(CampaignCreate(
            campaign_id=campaign_id, display_name="Table", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="turn",
            initial_state=(StateValue("example.piece-table", "table", saved),
                           StateValue("example.piece-table", "state", {"used": False})),
        ))


SCENARIOS = {
    "coordinate": (coordinate, [{"op": "panels"}]),
    "response": (response, [
        {"op": "panels"},
        {"op": "panels", "after": 0},
        {"op": "panels", "after": 1},
        {"op": "panels", "after": -1},
        {"op": "panels", "after": 99},
        {"op": "panels", "after": True},
        {"op": "panels", "after": "1"},
        {"op": "panels", "after": 1.5},
        {"op": "panels", "holding": "gm"},
        {"op": "panels", "campaign": "nowhere"},
    ]),
    "network": (network, [{"op": "panels"}]),
    "gated": (gated, [{"op": "panels"}, {"op": "panels", "campaign": "open-campaign"}]),
    "table": (piece_table, [{"op": "panels"}, {"op": "panels", "campaign": "broken-campaign"}]),
}


def run(owner: AuthorityOwner, step: dict[str, object]) -> dict[str, object]:
    campaign = str(step.get("campaign", "campaign"))
    keywords = {"after_revision": step["after"]} if "after" in step else {}
    try:
        if step.get("holding"):
            with owner.agent_session_work(str(step["holding"])):
                value = owner.campaign_panels(campaign, **keywords)  # type: ignore[arg-type]
        else:
            value = owner.campaign_panels(campaign, **keywords)  # type: ignore[arg-type]
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": ordered(value)}


def checkpoint(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    scenarios = []
    for name, (setup, steps) in SCENARIOS.items():
        folder = OUT / name
        folder.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
            database = Path(scratch) / "authority.sqlite3"
            with AuthorityOwner.open(database, OWNER) as owner:
                setup(owner)
            checkpoint(database)
            (folder / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
            with AuthorityOwner.open(database, OWNER) as owner:
                runs = [{"step": step, "outcome": run(owner, step)} for step in steps]
            checkpoint(database)
            (folder / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        scenarios.append({"name": name, "runs": runs})
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "scenarios": scenarios}
    (OUT / "campaign_panel_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
