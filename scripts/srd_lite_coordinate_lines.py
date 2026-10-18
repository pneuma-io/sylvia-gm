"""Reuse directional creature qualification with an actual rectangular Line query."""

from copy import deepcopy

from .srd_control_behaviors import _param, _when
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _require, _set


def bind_rectangular_directional_query(runtime):
    """Retain default Cone behavior and add only the demonstrated rectangular query choice."""
    collector = runtime["tools"]["collect_srd_cone_creature_targets"]
    collector["parameters"] += [
        _param("rectangular", "boolean", required=False, default=False),
        _param("height_feet", "number", required=False, default=0, minimum=0, maximum=1000000),
        _param(
            "up",
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
        ),
    ]
    changed = 0
    for rows in list(lists(collector["effects"])):
        for index, row in enumerate(rows):
            query = row.get("spatial_query", {})
            if (
                query.get("operation") != "area"
                or query.get("query", {}).get("shape", {}).get("kind") != "cone"
            ):
                continue
            rectangle = deepcopy(row)
            rectangle["spatial_query"]["query"]["shape"] = {
                "kind": "prism",
                "origin": "{_srd_cone_origin}",
                "direction": "{_cone_axis3}",
                "up": "{_directional_up}",
                "length": "{length_feet}",
                "width": "{_cone_width}",
                "height": "{height_feet}",
            }
            rows[index] = _when(
                "rectangular == true",
                [
                    _require(
                        "height_feet > 0",
                        "Declare the Line vertical extent from the scene before casting; "
                        "its SRD profile fixes length and width but supplies no height.",
                    ),
                    _set("_directional_up", [0, 0, 1]),
                    _when(
                        "_cone_axis3[0] ** 2 + _cone_axis3[1] ** 2 < 0.000000000001",
                        [_set("_directional_up", [0, 1, 0])],
                    ),
                    _when(
                        "len(up) != 0",
                        [
                            _require(
                                "len(up) == 3", "Choose a three-dimensional Line up reference."
                            ),
                            _set("_directional_up", "up"),
                        ],
                    ),
                    rectangle,
                ],
                [deepcopy(row)],
            )
            changed += 1
    if changed != 1:
        raise ValueError("The qualified directional-area query boundary changed.")
