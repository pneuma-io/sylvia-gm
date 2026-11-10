"""Admitted TD authoring actions allocate continuity inside the real owner transaction."""

from asyncio import CancelledError
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.application.tactical_grid import project_tactical_grid
from sylvia_gm.builder._ruleset_source import _required_engine_features
from sylvia_gm.domain.authority import WriterRole, thaw_json_value
from sylvia_gm.domain.errors import AuthorityError, RulesetPreconditionRejected, StaleRevision
from sylvia_gm.spatial import SpatialEngine, SpatialValidationError, admit_spatial_scene
from tests.test_spatial_engine import _ray
from tests.test_spatial_grid_edge import edge_scene, orient
from tests.test_td_grid_edge_identity_owner import _inputs, _start
from tests.test_td_grid_edge_owner import NS, candidate  # noqa: F401


def segment(start=2, end=3, horizontal=False, hp=12, visible=True):
    value = deepcopy(edge_scene(horizontal)["geometry"][0])
    value["volume"]["min"] = orient([2, start, 0], horizontal)
    value["volume"]["max"] = orient([2, end, 2], horizontal)
    value["metadata"]["cover_hp"] = hp
    value["metadata"]["material_resistance"] = hp // 4
    value["metadata"]["private"] = f"private-material-{hp}"
    if not visible:
        value["metadata"].pop("player")
    for key in ("id", "map_id", "edge_layer_id"):
        value.pop(key, None)
    return value


def setup_owner(tmp_path, archive, geometry=()):
    initial, character, record = _inputs(archive)
    scene = initial[NS + ".state"]["spatial_scene"]
    scene["geometry"] = deepcopy(list(geometry))
    scene["zones"] = []
    for item in scene["geometry"]:
        item["map_id"] = scene["map"]["id"]
    database = tmp_path / "wall.sqlite3"
    _start(database, archive, initial, character, record)
    return database


def state(owner):
    return thaw_json_value(owner.snapshot("identity").state[NS + ".state"])


def edit(owner, operation, ids=(), segments=(), *, key, revision=None):
    before = owner.snapshot("identity")
    scene = state(owner)["spatial_scene"]
    command = ActionCommand(
        campaign_id="identity",
        action_id=key,
        idempotency_key=key,
        owner_id="identity-owner",
        writer=WriterRole.DM,
        expected_revision=before.revision,
        turn_id="turn",
        package_id=NS,
        tool_name="author_wall",
        parameters={
            "map_id": scene["map"]["id"],
            "expected_revision": scene["map"]["revision"] if revision is None else revision,
            "operation": operation,
            "geometry_ids": list(ids),
            "segments": list(segments),
        },
    )
    return command, owner.execute_action(command)


def ray(scene, horizontal=False, reverse=False, along=3, diagonal=False):
    a, b = [1.5, along, 1], [2.5, along, 1]
    if diagonal:
        a[1] -= 0.5
        b[1] += 0.5
    if reverse:
        a, b = b, a
    query = _ray(orient(a, horizontal), orient(b, horizontal))
    query.update(map_id=scene["map"]["id"], include=["geometry"])
    return SpatialEngine(scene).query(query).as_payload()["intersections"]


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_draw_extend_connect_separate_replay_reopen_and_recovery(
    candidate,  # noqa: F811
    tmp_path,
    horizontal,
    reverse,  # noqa: F811
):
    legacy = deepcopy(edge_scene(horizontal)["geometry"][0])
    legacy["id"] = "unrelated-legacy"
    legacy["volume"]["min"] = orient([5, 2, 0], horizontal)
    legacy["volume"]["max"] = orient([5, 3, 2], horizontal)
    legacy.pop("occupies_cells")
    database = setup_owner(tmp_path, candidate, [legacy])
    backup = tmp_path / "recovery.sqlite3"
    with AuthorityOwner.open(database, "identity-owner") as owner:
        original = owner.snapshot("identity")
        saved_legacy = deepcopy(state(owner)["spatial_scene"]["geometry"][0])
        pieces = [segment(2, 3, horizontal, 12), segment(3, 4, horizontal, 20)]
        if reverse:
            pieces.reverse()
        command, receipt = edit(owner, "draw", segments=pieces, key="draw")
        assert receipt.status.value == "committed"
        drawn = owner.snapshot("identity")
        scene = state(owner)["spatial_scene"]
        assert scene["geometry"][0] == saved_legacy
        created = scene["geometry"][1:]
        ids = [item["id"] for item in created]
        layer = created[0]["edge_layer_id"]
        assert len(set(ids)) == 2 and all(item["edge_layer_id"] == layer for item in created)
        assert len(ray(scene, horizontal, reverse)) == 1
        owner_id = ray(scene, horizontal, reverse)[0]["id"]
        owner_segment = next(item for item in created if item["id"] == owner_id)
        assert owner_segment["metadata"]["cover_hp"] == 20
        assert owner_segment["metadata"]["material_resistance"] == 5
        diagonal_id = ray(scene, horizontal, reverse, diagonal=True)[0]["id"]
        diagonal_owner = next(item for item in created if item["id"] == diagonal_id)
        assert diagonal_owner["metadata"]["cover_hp"] == (20 if reverse else 12)
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("identity") == drawn
        # Per-segment metadata and stable geometry IDs survive an explicit extension.
        anchor = next(item for item in created if item["volume"]["max"][int(not horizontal)] == 4)
        edit(owner, "extend", [anchor["id"]], [segment(4, 5, horizontal, 28)], key="extend")
        extended = state(owner)["spatial_scene"]
        assert extended["map"]["revision"] == scene["map"]["revision"] + 1
        assert extended["geometry"][:3] == scene["geometry"]
        assert extended["geometry"][-1]["edge_layer_id"] == layer
        # A second deliberate draw stays independent despite identical appearance/coordinates.
        edit(owner, "draw", segments=[segment(3, 4, horizontal, 20)], key="independent")
        separate = state(owner)["spatial_scene"]["geometry"][-1]
        assert separate["edge_layer_id"] != layer
        assert len(ray(state(owner)["spatial_scene"], horizontal, reverse)) == 2
        # A third wall joins the complete selected first wall in one map revision.
        edit(owner, "draw", segments=[segment(5, 6, horizontal, 36)], key="third")
        before_connect = state(owner)["spatial_scene"]
        chosen = [
            item["id"] for item in before_connect["geometry"] if item.get("edge_layer_id") == layer
        ] + [before_connect["geometry"][-1]["id"]]
        connect, connected_receipt = edit(owner, "connect", chosen, key="connect")
        connected = state(owner)["spatial_scene"]
        connected_layer = next(
            item["edge_layer_id"] for item in connected["geometry"] if item["id"] in chosen
        )
        assert all(
            item["edge_layer_id"] == connected_layer
            for item in connected["geometry"]
            if item["id"] in chosen
        )
        assert connected["map"]["revision"] == before_connect["map"]["revision"] + 1
        for before, after in zip(before_connect["geometry"], connected["geometry"], strict=True):
            assert {k: v for k, v in before.items() if k != "edge_layer_id"} == {
                k: v for k, v in after.items() if k != "edge_layer_id"
            }
        assert (
            next(item for item in connected["geometry"] if item["id"] == separate["id"]) == separate
        )
        assert owner.execute_action(connect) == connected_receipt
        # Explicit separation is identity-only; each selected piece becomes independent.
        edit(owner, "separate", ids, key="separate")
        final = owner.snapshot("identity")
        final_scene = state(owner)["spatial_scene"]
        assert (
            len({item["edge_layer_id"] for item in final_scene["geometry"] if item["id"] in ids})
            == 2
        )
        assert len(ray(final_scene, horizontal, reverse)) == 3
        assert final.state[NS + ".entities"] == original.state[NS + ".entities"]
        assert final_scene["geometry"][0] == saved_legacy
        owner.backup_database(backup)
    for selected in (database, backup):
        with AuthorityOwner.open(selected, "identity-owner") as owner:
            assert owner.snapshot("identity") == final
            assert owner.execute_action(command) == receipt
            assert owner.execute_action(connect) == connected_receipt
            assert owner.snapshot("identity") == final


@pytest.mark.parametrize("operation", ["connect", "separate"])
@pytest.mark.parametrize("horizontal", [False, True])
def test_legacy_ambiguity_is_unchanged_until_explicit_owner_repair(
    candidate,  # noqa: F811
    tmp_path,
    operation,
    horizontal,  # noqa: F811
):
    legacy = []
    for index in range(2):
        piece = segment(2 + index, 3 + index, horizontal)
        piece.update(id=f"legacy-{index}", map_id="unused")
        piece.pop("occupies_cells")
        legacy.append(piece)
    database = setup_owner(tmp_path, candidate, legacy)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        before = owner.snapshot("identity")
        old_scene = state(owner)["spatial_scene"]
        with pytest.raises(SpatialValidationError) as refusal:
            ray(old_scene, horizontal)
        assert refusal.value.code == "spatial_ambiguous_edge_join"
        assert owner.snapshot("identity") == before
    with AuthorityOwner.open(database, "identity-owner") as owner:
        assert owner.snapshot("identity") == before
        edit(owner, operation, [item["id"] for item in legacy], key="repair")
        repaired = state(owner)["spatial_scene"]
        assert len(ray(repaired, horizontal)) == (1 if operation == "connect" else 2)
        for old, new in zip(old_scene["geometry"], repaired["geometry"], strict=True):
            assert {k: v for k, v in new.items() if k != "edge_layer_id"} == old


@pytest.mark.parametrize(
    "case", ["missing", "duplicate", "disconnected", "partial-wall", "overlap"]
)
def test_refusal_and_stale_revision_leave_entire_owner_snapshot_unchanged(
    candidate,  # noqa: F811
    tmp_path,
    case,  # noqa: F811
):
    database = setup_owner(tmp_path, candidate)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        edit(owner, "draw", segments=[segment(2, 3), segment(3, 4)], key="draw")
        scene = state(owner)["spatial_scene"]
        ids = [item["id"] for item in scene["geometry"]]
        before = owner.snapshot("identity")
        args = {
            "missing": ("connect", [ids[0], "not-a-wall"], []),
            "duplicate": ("connect", [ids[0], ids[0]], []),
            "disconnected": ("extend", [ids[0]], [segment(6, 7)]),
            "partial-wall": ("connect", [ids[0], ids[1] + "missing"], []),
            "overlap": ("extend", [ids[0]], [segment(2, 3)]),
        }[case]
        if case == "partial-wall":
            edit(owner, "draw", segments=[segment(4, 5)], key="other")
            before = owner.snapshot("identity")
            args = ("connect", [ids[1], state(owner)["spatial_scene"]["geometry"][-1]["id"]], [])
        with pytest.raises(RulesetPreconditionRejected) as refusal:
            edit(owner, *args, key="refused")
        assert "Select" in refusal.value.safe_reason
        assert "edge_layer_id" not in refusal.value.safe_reason
        assert ids[0] not in refusal.value.safe_reason
        assert owner.snapshot("identity") == before
        with pytest.raises(RulesetPreconditionRejected, match="map changed"):
            edit(owner, "draw", segments=[segment()], key="stale", revision=0)
        assert owner.snapshot("identity") == before


def test_actual_declared_producer_privacy_schema_and_writer_boundary(candidate, tmp_path):  # noqa: F811
    database = setup_owner(tmp_path, candidate)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        command, receipt = edit(
            owner, "draw", segments=[segment(), segment(3, 4, visible=False)], key="draw"
        )
        scene = state(owner)["spatial_scene"]
        public = project_tactical_grid({"scene": scene, "selection": {}, "preview": {}})
        rendered = str(public)
        assert "private-material" not in rendered and "edge_layer_id" not in rendered
        hidden = scene["geometry"][-1]["id"]
        assert hidden not in rendered
        before = owner.snapshot("identity")
        with pytest.raises(AuthorityError):
            owner.execute_action(
                replace(
                    command,
                    action_id="player",
                    idempotency_key="player",
                    writer=WriterRole.BUILDER,
                    expected_revision=before.revision,
                )
            )
        assert owner.snapshot("identity") == before
        assert receipt.status.value == "committed"
        assert receipt.result["context"] == {"operation": "draw"}
        assert "private-material" not in str(receipt.result["context"])
    _, _, record = _inputs(candidate)
    declaration = next(item for item in record["tools"] if item["name"] == "author_wall")
    assert declaration["writers"] == ["dm"]
    assert declaration["capability"] == "trigger-discipline.map-authoring"
    assert "edge_layer_id" not in declaration["input_schema"]["properties"]
    assert "spatial_wall_authoring.v1" in record["compatibility"]["requires"]
    features = _required_engine_features({"spatial_mutation": {"operation": "{operation}"}})
    assert {"spatial_wall_authoring.v1", "spatial_edge_continuity.v1"} <= set(features)
    # Standalone legacy edges still admit without any continuity identity.
    assert "edge_layer_id" not in admit_spatial_scene(edge_scene()).as_payload()["geometry"][0]


@pytest.mark.parametrize("fault", ["partial-input", "after-staging", "cancelled"])
def test_owner_discards_partial_draw_and_retry_allocates_once(
    candidate,  # noqa: F811
    tmp_path,
    monkeypatch,
    fault,
):  # noqa: F811
    database = setup_owner(tmp_path, candidate)
    pieces = [segment(2, 3), segment(3, 4)]
    with AuthorityOwner.open(database, "identity-owner") as owner:
        before = owner.snapshot("identity")
        if fault == "partial-input":
            pieces[1]["volume"]["max"][0] = 2.2
            with pytest.raises(RulesetPreconditionRejected):
                edit(owner, "draw", segments=pieces, key="retry")
            pieces[1] = segment(3, 4)
        else:
            original = AuthorityMemoryStateBridge._record_spatial_evidence

            def fail_after_staging(self, **kwargs):
                if kwargs["operation"] == "scene_mutation":
                    if fault == "cancelled":
                        raise CancelledError("synthetic cancellation after staging")
                    raise RuntimeError("synthetic failure after staging")
                return original(self, **kwargs)

            with monkeypatch.context() as scoped:
                scoped.setattr(
                    AuthorityMemoryStateBridge, "_record_spatial_evidence", fail_after_staging
                )
                with pytest.raises(
                    CancelledError if fault == "cancelled" else RulesetPreconditionRejected
                ):
                    edit(owner, "draw", segments=pieces, key="retry")
        assert owner.snapshot("identity") == before
        command, receipt = edit(owner, "draw", segments=pieces, key="retry")
        after = owner.snapshot("identity")
        assert after.revision == before.revision + 1
        geometry = state(owner)["spatial_scene"]["geometry"]
        assert len(geometry) == len({item["id"] for item in geometry}) == 2
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("identity") == after
        stale = replace(
            command,
            action_id="stale-campaign",
            idempotency_key="stale-campaign",
            expected_revision=before.revision,
        )
        with pytest.raises(StaleRevision):
            owner.execute_action(stale)
        assert owner.snapshot("identity") == after
    with AuthorityOwner.open(database, "identity-owner") as owner:
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("identity") == after


@pytest.mark.parametrize("field", ["id", "map_id", "edge_layer_id", "occupies_cells", "volume"])
def test_new_segment_contract_refuses_technical_identity_and_invalid_geometry(
    candidate,  # noqa: F811
    tmp_path,
    field,  # noqa: F811
):
    database = setup_owner(tmp_path, candidate)
    invalid = segment()
    if field == "occupies_cells":
        invalid[field] = True
    elif field == "volume":
        invalid[field]["kind"] = "box"
    else:
        invalid[field] = "user-typed-technical-id"
    with AuthorityOwner.open(database, "identity-owner") as owner:
        before = owner.snapshot("identity")
        with pytest.raises(RulesetPreconditionRejected):
            edit(owner, "draw", segments=[invalid], key="invalid")
        assert owner.snapshot("identity") == before


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_actual_td_shot_consumes_authored_continuity_and_preserves_other_segment(
    candidate,  # noqa: F811
    tmp_path,
    horizontal,
    reverse,  # noqa: F811
):
    from sylvia_gm import dice

    initial, character, record = _inputs(candidate)
    actors = initial[NS + ".entities"]
    scene = initial[NS + ".state"]["spatial_scene"]
    scene["geometry"], scene["zones"] = [], []
    a, b = ([2.5, 3, 0], [1.5, 3, 0]) if reverse else ([1.5, 3, 0], [2.5, 3, 0])
    for identity, point in (("vesper", a), ("hostile_alpha", b)):
        direction = (-1 if reverse else 1) * (1 if identity == "vesper" else -1)
        position = orient(point, horizontal)
        actors[identity].update(
            position_cell=[int(v) for v in position[:2]],
            center=orient([*point[:2], 0.9], horizontal),
            muzzle=orient([point[0] + 0.41 * direction, point[1], 0.5], horizontal),
            ap=100,
        )
        next(row for row in scene["entities"] if row["id"] == identity).update(
            position=position, height=1.8
        )
    for item in scene["entities"]:
        if item["id"] not in ("vesper", "hostile_alpha"):
            item["active"] = False
    database = tmp_path / "shot.sqlite3"
    _start(database, candidate, initial, character, record)
    pieces = [segment(2, 3, horizontal, 12), segment(3, 4, horizontal, 20)]
    for item in pieces:
        item["volume"]["max"][2] = 0.6
        item["metadata"]["material_resistance"] = actors["vesper"]["weapon"]["material_pen"] + 1
    with AuthorityOwner.open(database, "identity-owner") as owner:
        edit(owner, "draw", segments=pieces, key="draw")
        drawn = deepcopy(state(owner)["spatial_scene"])
        owner_index = 1
        chosen = drawn["geometry"][owner_index]
        before = owner.snapshot("identity")
        shot = ActionCommand(
            campaign_id="identity",
            action_id="shot",
            idempotency_key="shot",
            owner_id="identity-owner",
            writer=WriterRole.DM,
            expected_revision=before.revision,
            turn_id="turn",
            package_id=NS,
            tool_name="fire_single",
            parameters={
                "attacker": "vesper",
                "target": "hostile_alpha",
                "aim_point": orient([*b[:2], 0.5], horizontal),
                "critical_effect": "DEEP_HIT",
            },
        )
        with patch.object(dice._rng, "randint", return_value=14):
            receipt = owner.execute_action(shot)
        after = state(owner)
        assert after["last_attack"]["cover_id"] == chosen["id"]
        assert after["last_attack"]["material_outcome"] == "STOP"
        retained = drawn["geometry"][1 - owner_index]
        assert (
            next(
                item for item in after["spatial_scene"]["geometry"] if item["id"] == retained["id"]
            )
            == retained
        )
        damaged = next(
            item for item in after["spatial_scene"]["geometry"] if item["id"] == chosen["id"]
        )
        assert damaged["id"] == chosen["id"] and damaged["edge_layer_id"] == chosen["edge_layer_id"]
        assert damaged["metadata"]["cover_hp"] < chosen["metadata"]["cover_hp"]
        assert owner.execute_action(shot) == receipt


@pytest.mark.parametrize("horizontal", [False, True])
def test_real_legacy_connection_retains_unselected_independent_barrier(
    candidate,  # noqa: F811
    tmp_path,
    horizontal,
):  # noqa: F811
    from tests.test_spatial_grid_edge_identity import mixed_join

    original = mixed_join(horizontal)["geometry"]
    database = setup_owner(tmp_path, candidate, original)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        before = deepcopy(state(owner)["spatial_scene"])
        edit(owner, "connect", ["b-left", "c-right"], key="repair")
        scene = state(owner)["spatial_scene"]
        assert scene["geometry"][0] == before["geometry"][0]
        for reverse in (False, True):
            assert {item["id"] for item in ray(scene, horizontal, reverse)} == {
                "a-steel",
                "c-right",
            }


@pytest.mark.parametrize("eligibility", ["opaque", "clear", "metadata", "missing"])
@pytest.mark.parametrize("horizontal", [False, True])
def test_authored_seam_exposure_respects_visibility_and_metadata(
    candidate,  # noqa: F811
    tmp_path,
    eligibility,
    horizontal,  # noqa: F811
):
    from tests.test_spatial_grid_edge_exposure import exposure_seam

    template, query, _ = exposure_seam(horizontal, eligibility=eligibility)
    initial, character, record = _inputs(candidate)
    scene = initial[NS + ".state"]["spatial_scene"]
    map_id = scene["map"]["id"]
    scene.update(geometry=[], entities=template["entities"], zones=[])
    for item in scene["entities"]:
        item["map_id"] = map_id
    query["map_id"] = map_id
    pieces = [
        {key: value for key, value in item.items() if key not in {"id", "map_id"}}
        for item in template["geometry"]
    ]
    database = tmp_path / "exposure.sqlite3"
    _start(database, candidate, initial, character, record)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        edit(owner, "draw", segments=pieces, key="draw")
        snapshot = owner.snapshot("identity")
        scene = state(owner)["spatial_scene"]
        result = SpatialEngine(scene).query(query).as_payload()
        assert result["exposure"] == (0 if eligibility == "opaque" else 1)
        assert len(result["occluders"]) == (1 if eligibility == "opaque" else 0)
        assert len(ray(scene, horizontal)) == 1
        assert owner.snapshot("identity") == snapshot


def test_extend_selected_untagged_edge_allocates_continuity_without_rewriting_legacy(
    candidate,  # noqa: F811
    tmp_path,  # noqa: F811
):
    old = deepcopy(edge_scene()["geometry"][0])
    old.pop("occupies_cells")
    database = setup_owner(tmp_path, candidate, [old])
    with AuthorityOwner.open(database, "identity-owner") as owner:
        before = deepcopy(state(owner)["spatial_scene"]["geometry"][0])
        edit(owner, "extend", [old["id"]], [segment(3, 4)], key="extend")
        scene = state(owner)["spatial_scene"]
        anchor, added = scene["geometry"]
        assert anchor["edge_layer_id"] == added["edge_layer_id"]
        assert {key: value for key, value in anchor.items() if key != "edge_layer_id"} == before
        assert len(ray(scene)) == 1
