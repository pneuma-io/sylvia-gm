"""A player's panel control through Python's owner: `player_action` with a `panel` source on
`tests/test_player_actions.py`'s synthetic coordinate-map control.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_panel_action_runs.py

Six scenarios, each on its own base (`panel_action/<scenario>/base.sqlite3.gz`, made by
`_create`) with its own final database (`final.sqlite3.gz`):

- `plain`: the move, its replay, a conflicting replay, an unknown surface and action, selections
  the map refuses, and a preview;
- `confirmed` (confirmation required): the preview, the move without its token, the confirmed
  move, its replay, and an old preview's token used at a later revision;
- `choice` (a route choice): the move reserving its choice, and its replay;
- `coordinate` (`tests/test_coordinate_map_ui.py`'s coordinate map): an exact 3D aim point, its
  replay, an aim off the map and a stale map revision;
- `reference` (`tests/test_coordinate_actor_references.py`'s declared actor reference): the move
  binding the selected body to its mechanical actor, and a body without a reference;
- `party` (`tests/test_party_action_control.py`'s single-character control, with a player control
  scope): party, map and fixed actions for an uncontrolled companion and a hidden member, the
  preview naming the controlled actor, then the controlled member's party and map moves.

Each step records its answer (a receipt, an awaiting-choice receipt or the preview) or the
failure's class, code and message. It writes `panel_action/panel_action_runs.json`;
`tests/panel_action.rs` replays every scenario on the Rust owner. Reference tooling only.
"""

from __future__ import annotations

import dataclasses
import enum
import gzip
import io
import json
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.application.authority import CampaignCreate  # noqa: E402
from sylvia_gm.domain.authority import StateValue, WriterRole, owned_payload  # noqa: E402
from tests.test_coordinate_actor_references import reference_archive, scene_with_actor_reference  # noqa: E402
from tests.test_coordinate_map_ui import coordinate_archive  # noqa: E402
from tests.test_party_action_control import _campaign as party_campaign  # noqa: E402
from tests.test_player_actions import _create  # noqa: E402
from tests.test_tactical_grid_ui import _scene  # noqa: E402

OUT = HERE / "panel_action"
OWNER = "owner"


def plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, object] = {"__class__": type(value).__name__}
        for field in dataclasses.fields(value):
            out[field.name] = plain(getattr(value, field.name))
        return out
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [plain(item) for item in value]
    return value


def checkpoint(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def request(**changes: object) -> dict[str, object]:
    out: dict[str, object] = {
        "campaign_id": "campaign", "session_id": "gm", "operation_id": "move", "expected_revision": 0,
        "source": {"kind": "panel", "surface_id": "operations.map", "action_id": "move"},
        "selection": {"actor": "operator", "destination": [4, 4], "map_revision": 7},
        "confirmation": None, "preview_only": False,
    }
    out.update(changes)
    return out


def scenario(name: str, setup, steps) -> dict[str, object]:  # noqa: ANN001
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        with AuthorityOwner.open(database, OWNER) as owner:
            setup(owner)
        checkpoint(database)
        (folder / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        with AuthorityOwner.open(database, OWNER) as owner:

            def act(step_name: str, step_request: dict[str, object]) -> object:
                answer = None
                try:
                    answer = owner.player_action(**step_request)
                    outcome: dict[str, object] = {"ok": plain(answer)}
                except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                    outcome = {"error": type(error).__name__, "code": getattr(error, "code", None),
                               "message": str(error)}
                runs.append({"name": step_name, "request": plain(step_request), "outcome": outcome})
                return answer

            steps(act)
        checkpoint(database)
        (folder / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    return {"name": name, "runs": runs}


def plain_steps(act) -> None:  # noqa: ANN001
    act("a preview", request(preview_only=True))
    act("the move", request())
    act("its replay", request())
    act("a conflicting replay", request(selection={"actor": "operator", "destination": [5, 4], "map_revision": 7}))
    act("an unknown surface", request(operation_id="other-surface", expected_revision=1,
                                      source={"kind": "panel", "surface_id": "nowhere", "action_id": "move"}))
    act("an unknown action", request(operation_id="other-action", expected_revision=1,
                                     source={"kind": "panel", "surface_id": "operations.map", "action_id": "fly"}))
    act("a destination off the map", request(operation_id="off-map", expected_revision=1,
                                             selection={"actor": "operator", "destination": [99, 99], "map_revision": 7}))
    act("a destination that is not a point", request(operation_id="not-a-point", expected_revision=1,
                                                     selection={"actor": "operator", "destination": "north", "map_revision": 7}))
    act("a stale revision", request(operation_id="stale", expected_revision=0))


def confirmed_steps(act) -> None:  # noqa: ANN001
    preview = act("the preview", request(preview_only=True))
    token = preview["confirmation"]  # type: ignore[index]
    act("the move without its token", request())
    act("the confirmed move", request(confirmation=token))
    act("its replay", request(confirmation=token))
    later = act("a later preview", request(operation_id="later", expected_revision=1, preview_only=True))
    act("an old token at the later revision", request(operation_id="later", expected_revision=1, confirmation=token))
    act("the later move confirmed", request(operation_id="later", expected_revision=1,
                                            confirmation=later["confirmation"]))  # type: ignore[index]


def choice_steps(act) -> None:  # noqa: ANN001
    act("the move reserving its choice", request())
    act("its replay", request())
    act("a preview while it waits", request(preview_only=True))


def coordinate_setup(owner: AuthorityOwner, archive: bytes = b"", scene: object = None) -> None:
    installed = owner.install_package_archive(io.BytesIO(archive or coordinate_archive()),
                                              install_id="coordinate-install")
    owner.create_campaign(CampaignCreate(
        campaign_id="campaign", display_name="Synthetic coordinate test", writer=WriterRole.DM,
        package_lock=installed.package_lock, turn_id="coordinate-turn",
        initial_state=(StateValue("synthetic.coordinate", "state", {
            "spatial_scene": scene if scene is not None else _scene(),
            "last_actor": None, "last_destination": None}),)))


def coordinate_steps(act) -> None:  # noqa: ANN001
    aim = {"actor": "operator", "aim_point": [2.125, 1.375, 0.75], "map_revision": 7}
    act("the coordinate move", request(session_id=None, operation_id="coordinate-move", selection=owned_payload(aim)))
    act("its replay", request(session_id=None, operation_id="coordinate-move", selection=owned_payload(aim)))
    act("an aim point off the map", request(session_id=None, operation_id="off-map", expected_revision=1,
                                            selection={**aim, "aim_point": [99.0, 99.0, 0.0]}))
    act("a stale map revision", request(session_id=None, operation_id="stale-map", expected_revision=1,
                                        selection={**aim, "map_revision": 6}))


def reference_steps(act) -> None:  # noqa: ANN001
    aim = {"actor": "operator", "aim_point": [2.125, 1.375, 0.75], "map_revision": 7}
    act("a preview naming the mechanical actor", request(session_id=None, operation_id="refs-move", selection=aim,
                                                        preview_only=True))
    act("the move bound to its mechanical actor", request(session_id=None, operation_id="refs-move", selection=aim))
    act("a body without a reference", request(session_id=None, operation_id="refs-ambusher", expected_revision=1,
                                              selection={**aim, "actor": "ambusher"}))


def party_steps(act) -> None:  # noqa: ANN001
    party = {"kind": "panel", "surface_id": "party", "action_id": "move"}
    act("an uncontrolled companion", request(operation_id="party-bravo", source=party, selection={"actor": "bravo"}))
    act("a hidden member", request(operation_id="party-hidden", source=party, selection={"actor": "hidden"}))
    act("the map for an uncontrolled companion", request(operation_id="map-bravo",
                                                         selection={"actor": "bravo", "destination": [4, 4], "map_revision": 7}))
    act("a fixed action for an uncontrolled companion", request(
        operation_id="fixed-bravo", selection=None,
        source={"kind": "panel", "surface_id": "party", "action_id": "move-bravo"}))
    act("the party preview with its actor", request(operation_id="party-operator", source=party,
                                                    selection={"actor": "operator"}, preview_only=True))
    act("the controlled member", request(operation_id="party-operator", source=party, selection={"actor": "operator"}))
    act("the map for the controlled member", request(operation_id="map-operator", expected_revision=1,
                                                     selection={"actor": "operator", "destination": [4, 4], "map_revision": 7}))


def main() -> None:
    OUT.mkdir(exist_ok=True)
    scenarios = [
        scenario("plain", lambda owner: _create(owner), plain_steps),
        scenario("confirmed", lambda owner: _create(owner, controls={"confirmation": "required"}), confirmed_steps),
        scenario("choice", lambda owner: _create(owner, choice=True), choice_steps),
        scenario("coordinate", coordinate_setup, coordinate_steps),
        scenario("reference", lambda owner: coordinate_setup(owner, reference_archive(), scene_with_actor_reference()),
                 reference_steps),
        scenario("party", lambda owner: party_campaign(owner, "single"), party_steps),
    ]
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "scenarios": scenarios}
    (OUT / "panel_action_runs.json").write_bytes(
        (json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
