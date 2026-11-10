"""Pure owner-control fixtures without optional transport/provider imports."""


def _checks(owner, *, revision=0, actor="operator", destination=None):
    return owner.player_action_availability(
        campaign_id="campaign",
        expected_revision=revision,
        requests=[
            {
                "source": {"kind": "panel", "surface_id": "operations.map", "action_id": "move"},
                "selection": {
                    "actor": actor,
                    "destination": destination or [4, 4],
                    "map_revision": 7,
                },
            }
        ],
    )["results"][0]


def _route_controls():
    return {
        "queries": [
            {
                "spatial_query": {
                    "scene": "state.spatial_scene",
                    "operation": "path",
                    "query": {
                        "map_id": "training-floor",
                        "start": [1, 2],
                        "goal": "{destination}",
                        "moving_entity_id": "{actor}",
                        "allow_diagonal": True,
                        "corner_policy": "forbid_cutting",
                        "include_entities": True,
                        "max_steps": 256,
                    },
                },
                "store_as": "_path",
            }
        ],
        "calculations": [
            {
                "set": {"field": "_route_ap", "value": "(_path.cost + 1.999999) // 2"},
                "condition": "_path.found == true",
            }
        ],
        "checks": [
            {"condition": "_path.found == true", "reason": "No route is currently available"},
            {"condition": "_route_ap <= 1", "reason": "This route costs too many action points"},
        ],
    }
