"""Package-owned identities can be created without replacing existing state or participants."""

import pytest

from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ToolDef, ToolParameter


def _create(bridge, identity="relay-a", charge=4):
    tool = ToolDef(
        name="place_relay",
        parameters=[ToolParameter("identity", "string"), ToolParameter("charge", "integer")],
        effects=[{"create_entity": {
            "id": "identity", "value": {"label": "'North relay'", "charge": "charge"},
        }}],
    )
    return execute_tool(
        tool.name, {"identity": identity, "charge": charge}, {tool.name: tool}, bridge, {}, {}, {},
    )


def test_neutral_creation_stays_in_its_package_and_never_resets_existing_state():
    bridge = AuthorityMemoryStateBridge("example.signal", {
        "other.package.entities": {"relay-a": {"charge": 90}},
    })
    created = _create(bridge)
    assert created.success, created.error
    assert bridge.get_entity("relay-a") == {"label": "North relay", "charge": 4}
    (mutation,) = bridge.mutations()
    assert (mutation.namespace, mutation.key) == ("example.signal", "entities")

    bridge.write_field("relay-a", "charge", 1)
    repeated = _create(bridge, charge=99)
    assert not repeated.success and repeated.failure_kind == "precondition"
    assert "already exists" in repeated.error
    assert bridge.get_entity("relay-a")["charge"] == 1


@pytest.mark.parametrize("identity", ["state", "combat", "entities", "player", " bad ", ""])
def test_reserved_or_participant_identity_cannot_be_created(identity):
    bridge = AuthorityMemoryStateBridge("example.signal", {}, {
        "player": {"package_id": "example.signal", "data": {"callsign": "North"}},
    })
    result = _create(bridge, identity)
    assert not result.success
    assert bridge.mutations() == ()
    assert bridge.participant_mutations() == ()
