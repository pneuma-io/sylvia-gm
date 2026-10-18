"""Native TD panel repairs preserve authored mechanics and persistence."""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"


def run_script(name, output, *args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/qa" / name), "--output", str(output), *args],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads((output / "proof.json").read_text())


def test_native_character_prices_scope_retry_and_reopen(tmp_path):
    proof = run_script("verify_td_native_panels.py", tmp_path / "native")
    assert [(v["action"], v["cost"], v["actual_ap_delta"]) for v in proof["steps"]] == [
        ("stance-crouched", 1, 1),
        ("stance-standing", 1, 1),
        ("reload-retain", 2, 2),
    ]
    assert "not currently controlled" in proof["enemy_rejection"]
    assert proof["reopen_exact"]


def test_authored_low_edges_qualify_native_shots_from_both_angles(tmp_path):
    proof = run_script("verify_td_authored_cover.py", tmp_path / "cover")
    assert {(v["orientation"], v["angle"], v["exposure"]["exposure"]) for v in proof["cases"]} == {
        (orientation, angle, value)
        for orientation in ["vertical", "horizontal"]
        for angle, value in [("protected", 0.0), ("open", 1.0)]
    }
    assert all(v["actual_ap_delta"] == 2 for v in proof["cases"])
    assert proof["reopen_exact"]


def test_package_builder_preserves_native_bodies_and_uses_authored_presets(tmp_path):
    proof = run_script(
        "build_td_panel_candidate.py",
        tmp_path / "package",
        "--input",
        str(FIXTURES / "td-native-source-v23.sylvia-gm-package"),
    )
    assert len(proof["costs"]) == 17
    assert proof["native_execution_bodies_and_definitions_exact"]
    with zipfile.ZipFile(FIXTURES / "td-native-source-v23.sylvia-gm-package") as z:
        original = yaml.safe_load(
            z.read("packages/pneuma-io.trigger-discipline/runtime/ruleset.yaml")
        )
    with zipfile.ZipFile(
        tmp_path / "package/Trigger-Discipline-Panels-0.1.6.sylvia-gm-package"
    ) as z:
        candidate = yaml.safe_load(
            z.read("packages/pneuma-io.trigger-discipline/runtime/ruleset.yaml")
        )
        authored = json.loads(
            z.read("packages/pneuma-io.trigger-discipline/conformance/panel-edge-combat.json")
        )
    for name, tool in candidate["tools"].items():
        assert {k: v for k, v in tool.items() if k != "metadata"} == {
            k: v for k, v in original["tools"][name].items() if k != "metadata"
        }
    assert candidate["definitions"] == original["definitions"]
    assert (
        candidate["tools"]["change_stance"]["metadata"]["player_controls"]["calculations"][-1][
            "set"
        ]["value"]
        == "definitions.parameters.move.stance_change_ap"
    )
    geometry = {
        v["id"]: v
        for v in authored["initial_state"]["pneuma-io.trigger-discipline.state"]["spatial_scene"][
            "geometry"
        ]
    }
    for name, preset in [("quarter-edge-vertical", "quarter"), ("half-edge-horizontal", "half")]:
        assert geometry[name]["occupies_cells"] is False
        assert geometry[name]["volume"]["max"][2] == proof["heights_from_native_presets"][preset]
    assert geometry["occupied-crate"]["occupies_cells"] is True


def test_every_declared_cost_uses_native_readonly_context_and_variable_prices(tmp_path):
    from copy import deepcopy

    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.application.package_runtime import materialize_player_action_previewer
    from sylvia_gm.domain.authority import thaw_json_value
    from sylvia_gm.domain.package_library import ToolDeclaration

    run_script("verify_td_native_panels.py", tmp_path / "costs")
    with zipfile.ZipFile(FIXTURES / "td-native-panels-0.1.6.sylvia-gm-package") as z:
        runtime_bytes = z.read("packages/pneuma-io.trigger-discipline/runtime/ruleset.yaml")
    ns = "pneuma-io.trigger-discipline"
    with AuthorityOwner.open(tmp_path / "costs/campaign.sqlite3", "readonly-preview-test") as owner:
        before = owner.snapshot("td-native")
        state = thaw_json_value(before.state)
        participants = {v.participant_id: thaw_json_value(v.data) for v in before.participants}
        base = {
            "actor": "hex",
            "attacker": "hex",
            "target": "hostile_alpha",
            "aim_point": state[ns + ".entities"]["hostile_alpha"]["center"],
            "ap": 1,
            "stance": "CROUCHED",
            "destination": [3, 3],
            "critical_effect": "DEVASTATING_HIT",
            "rounds": 5,
            "disposition": "RETAIN",
            "drone": "fpv_alpha",
            "landing_point": state[ns + ".entities"]["hostile_alpha"]["center"],
            "zone_id": "overwatch-zone",
            "node_id": "security_controller",
            "action": "CONNECT",
            "method": "INTRUDE",
            "route_step": ["lobby_terminal", "camera_gateway", "terminal_gateway"],
            "remote_function": "open_security_door",
        }
        expected = {
            "aim": 1,
            "autodrone_fire": 2,
            "change_stance": 1,
            "cycle_weapon": 0,
            "establish_suppression": 2,
            "fire_burst": 2,
            "fire_full_auto": 1,
            "fire_single": 2,
            "fpv_fragmentation_strike": 1,
            "intrude_network": 2,
            "move_actor": 1,
            "network_connection": 0,
            "operate_remote_function": 1,
            "pivot_network": 2,
            "prepare_overwatch": 2,
            "recover_recoil": 1,
            "reload_weapon": 2,
        }
        tools = {v.name: v for v in owner.catalog("td-native")}

        def price(name, replacement=None, current_state=None, current_runtime=None):
            tool = tools[name]
            args = {k: v for k, v in base.items() if k in tool.input_schema["properties"]}
            if name == "network_connection":
                args["node_id"] = "lobby_terminal"
            if name == "intrude_network":
                args.update(node_id="camera_gateway", action="GET_IN")
            if name == "pivot_network":
                args["node_id"] = "camera_gateway"
            args.update(replacement or {})
            declaration = ToolDeclaration(name, tool.capability, tool.input_schema, (), (), ())
            result = materialize_player_action_previewer(
                package_id=ns,
                declaration=declaration,
                runtime_bytes=current_runtime or runtime_bytes,
            )(args, current_state or state, participants)
            assert result is not None, name
            rows = [v["value"] for v in result["preview"]["summary"] if v["label"] == "AP cost"]
            assert len(rows) == 1 and isinstance(rows[0], (int, float)), (name, result)
            return rows[0]

        assert {name: price(name) for name in expected} == expected
        assert price("aim", {"ap": 2}) == 2
        assert price("fire_full_auto", {"rounds": 11}) == 3
        assert price("network_connection", {"action": "DISCONNECT"}) == 0
        altered_state = deepcopy(state)
        altered_state[ns + ".entities"]["hex"]["weapon"]["reload_ap"] = 4
        assert price("reload_weapon", current_state=altered_state) == 4
        altered_runtime = yaml.safe_load(runtime_bytes)
        altered_runtime["definitions"]["parameters"]["move"]["stance_change_ap"] = 3
        assert (
            price(
                "change_stance",
                current_runtime=yaml.safe_dump(altered_runtime, sort_keys=False).encode(),
            )
            == 3
        )
        assert owner.snapshot("td-native") == before
