"""Machine-readable definitions and neutral examples for maps and result cards."""

from __future__ import annotations

_IDENTITY = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$(?![\s\S])"


SPATIAL_SCENE_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:sylvia-gm:content:spatial-scene:1",
    "title": "Sylvia GM spatial scene v1",
    "description": (
        "Ruleset-owned, Ruleset-neutral map geometry and spatial state. The shared spatial "
        "engine interprets coordinates, obstruction and visibility; the Ruleset owns their "
        "gameplay meaning and consequences."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["format", "map", "geometry", "entities", "zones", "terrain"],
    "properties": {
        "format": {"type": "integer", "const": 1},
        "map": {"$ref": "#/$defs/map"},
        "geometry": {
            "type": "array",
            "maxItems": 20000,
            "items": {"$ref": "#/$defs/geometry"},
        },
        "entities": {
            "type": "array",
            "maxItems": 5000,
            "items": {"$ref": "#/$defs/entity"},
        },
        "zones": {
            "type": "array",
            "maxItems": 5000,
            "items": {"$ref": "#/$defs/zone"},
        },
        "terrain": {
            "type": "array",
            "maxItems": 100000,
            "items": {"$ref": "#/$defs/terrain"},
        },
    },
    "$defs": {
        "identity": {"type": "string", "pattern": _IDENTITY},
        "number": {
            "type": "number",
            "minimum": -1000000,
            "maximum": 1000000,
        },
        "positiveNumber": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 1000000,
        },
        "coordinate2": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": {"$ref": "#/$defs/number"},
        },
        "coordinate3": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"$ref": "#/$defs/number"},
        },
        "cell": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": {"type": "integer", "minimum": 0, "maximum": 2047},
        },
        "bounds": {
            "type": "object",
            "additionalProperties": False,
            "required": ["min", "max"],
            "properties": {
                "min": {"$ref": "#/$defs/coordinate3"},
                "max": {"$ref": "#/$defs/coordinate3"},
            },
        },
        "grid": {
            "type": "object",
            "additionalProperties": False,
            "required": ["cell_size", "unit", "origin"],
            "properties": {
                "cell_size": {"$ref": "#/$defs/positiveNumber"},
                "unit": {"$ref": "#/$defs/identity"},
                "origin": {"$ref": "#/$defs/coordinate2"},
            },
        },
        "layer": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "min_elevation", "max_elevation"],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "min_elevation": {"$ref": "#/$defs/number"},
                "max_elevation": {"$ref": "#/$defs/number"},
            },
        },
        "map": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "revision", "bounds", "grid", "layers"],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "revision": {"type": "integer", "minimum": 0, "maximum": 9223372036854775807},
                "bounds": {"$ref": "#/$defs/bounds"},
                "grid": {"$ref": "#/$defs/grid"},
                "layers": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 128,
                    "items": {"$ref": "#/$defs/layer"},
                },
            },
        },
        "movement": {"type": "string", "enum": ["passable", "blocking"]},
        "visibility": {"type": "string", "enum": ["clear", "opaque"]},
        "box": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "min", "max"],
            "properties": {
                "kind": {"const": "box"},
                "min": {"$ref": "#/$defs/coordinate3"},
                "max": {"$ref": "#/$defs/coordinate3"},
            },
        },
        "cylinder": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "center", "radius", "height"],
            "properties": {
                "kind": {"const": "cylinder"},
                "center": {"$ref": "#/$defs/coordinate3"},
                "radius": {"$ref": "#/$defs/positiveNumber"},
                "height": {"$ref": "#/$defs/positiveNumber"},
            },
        },
        "shape": {"oneOf": [{"$ref": "#/$defs/box"}, {"$ref": "#/$defs/cylinder"}]},
        "metadata": {
            "type": "object",
            "description": (
                "The map's declared extension area. Rulesets may store finite JSON here. "
                "Player projection reads only a closed metadata.player presentation record."
            ),
        },
        "destroyed": {
            "type": "object",
            "additionalProperties": False,
            "required": ["active", "movement", "visibility"],
            "properties": {
                "active": {"type": "boolean"},
                "movement": {"$ref": "#/$defs/movement"},
                "visibility": {"$ref": "#/$defs/visibility"},
            },
        },
        "geometry": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id",
                "map_id",
                "volume",
                "active",
                "movement",
                "visibility",
                "metadata",
                "destroyed",
            ],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "map_id": {"$ref": "#/$defs/identity"},
                "volume": {"$ref": "#/$defs/box"},
                "active": {"type": "boolean"},
                "movement": {"$ref": "#/$defs/movement"},
                "occupies_cells": {
                    "type": "boolean",
                    "default": True,
                    "description": "Omission is admitted as true for older scenes.",
                },
                "visibility": {"$ref": "#/$defs/visibility"},
                "metadata": {"$ref": "#/$defs/metadata"},
                "destroyed": {"$ref": "#/$defs/destroyed"},
            },
        },
        "boxFootprint": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "half_extents"],
            "properties": {
                "kind": {"const": "box"},
                "half_extents": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"$ref": "#/$defs/positiveNumber"},
                },
            },
        },
        "circleFootprint": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "radius"],
            "properties": {
                "kind": {"const": "circle"},
                "radius": {"$ref": "#/$defs/positiveNumber"},
            },
        },
        "entity": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id",
                "map_id",
                "position",
                "footprint",
                "height",
                "facing",
                "active",
                "movement",
                "visibility",
                "metadata",
            ],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "map_id": {"$ref": "#/$defs/identity"},
                "position": {"$ref": "#/$defs/coordinate3"},
                "footprint": {
                    "oneOf": [
                        {"$ref": "#/$defs/boxFootprint"},
                        {"$ref": "#/$defs/circleFootprint"},
                    ]
                },
                "height": {"$ref": "#/$defs/positiveNumber"},
                "facing": {"$ref": "#/$defs/coordinate2"},
                "active": {"type": "boolean"},
                "movement": {"$ref": "#/$defs/movement"},
                "visibility": {"$ref": "#/$defs/visibility"},
                "metadata": {"$ref": "#/$defs/metadata"},
            },
        },
        "zone": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "map_id", "shape", "metadata"],
            "properties": {
                "id": {"$ref": "#/$defs/identity"},
                "map_id": {"$ref": "#/$defs/identity"},
                "shape": {"$ref": "#/$defs/shape"},
                "metadata": {"$ref": "#/$defs/metadata"},
            },
        },
        "terrain": {
            "type": "object",
            "additionalProperties": False,
            "required": ["cell", "layer_id", "cost"],
            "properties": {
                "cell": {"$ref": "#/$defs/cell"},
                "layer_id": {"$ref": "#/$defs/identity"},
                "cost": {"$ref": "#/$defs/positiveNumber"},
            },
        },
    },
}


SPATIAL_SCENE_EXAMPLE: dict[str, object] = {
    "format": 1,
    "map": {
        "id": "relay-floor",
        "revision": 1,
        "bounds": {"min": [0, 0, 0], "max": [8, 6, 3]},
        "grid": {"cell_size": 1, "unit": "m", "origin": [0, 0]},
        "layers": [{"id": "floor", "min_elevation": 0, "max_elevation": 3}],
    },
    "geometry": [
        {
            "id": "bulkhead",
            "map_id": "relay-floor",
            "volume": {"kind": "box", "min": [3, 2, 0], "max": [3.25, 4, 2.5]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": True,
            "visibility": "opaque",
            "metadata": {"player": {"label": "Bulkhead", "kind": "door"}},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    ],
    "entities": [
        {
            "id": "keeper",
            "map_id": "relay-floor",
            "position": [1.5, 2.5, 0],
            "footprint": {"kind": "circle", "radius": 0.4},
            "height": 1.8,
            "facing": [1, 0],
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {"player": {"label": "Keeper", "kind": "unit"}},
        }
    ],
    "zones": [],
    "terrain": [{"cell": [2, 2], "layer_id": "floor", "cost": 2}],
}


SPATIAL_SCENE_NOTES: tuple[str, ...] = (
    (
        "All objects except metadata are closed. metadata is the deliberate Ruleset extension "
        "area and is limited to 16 KiB of finite canonical JSON per record."
    ),
    (
        "The complete scene is limited to 24 JSON levels and 100,000 JSON nodes. Coordinates are "
        "finite and have an absolute limit of 1,000,000."
    ),
    (
        "Every bounds minimum must be less than its corresponding maximum. Grid origin equals the "
        "lower x/y bounds. Each axis has at most 2,048 cells and the grid has at most 4,000,000 "
        "cells in total."
    ),
    (
        "Layer IDs are unique and their elevation ranges are positive and inside map bounds. "
        "Geometry, entity and zone IDs are unique across all three collections, and every map_id "
        "equals map.id."
    ),
    (
        "Volumes, entity footprints and zone shapes must have positive extents and fit inside map "
        "bounds. Entity facing must be nonzero. Terrain cells must fit the grid, name a declared "
        "layer and be unique per layer."
    ),
    (
        "Player map projection exposes an item only when metadata.player is a valid closed display "
        "record. That record requires label and kind; it may contain state, stance, side, color, "
        "icon, interactive and height_label. Hidden metadata stays owner-only."
    ),
    (
        "Tactical objectives, selection and preview belong to the tactical-grid surface source, "
        "outside this scene object. The renderer filters and bounds them. Selections "
        "are revalidated against the current projection and authoritative scene."
    ),
)


TACTICAL_GRID_SOURCE_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:sylvia-gm:content:tactical-grid-source:1",
    "title": "Sylvia GM tactical-grid surface source v1",
    "description": (
        "Ruleset-owned source object resolved by a tactical_grid or coordinate_map panel. "
        "Coordinate maps use continuous aim_point input rather than destination cells. scene uses the spatial "
        "scene definition; the other fields are optional player presentation and selection state."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["scene"],
    "properties": {
        "scene": {
            "type": "object",
            "description": "A complete value satisfying the spatial_scene content definition.",
        },
        "enabled": {
            "type": ["boolean", "null"],
            "description": "A coordinate_map with this field present is shown only when true.",
        },
        "objectives": {
            "type": ["array", "null"],
            "maxItems": 256,
            "items": {"$ref": "#/$defs/objective"},
        },
        "selection": {
            "oneOf": [{"$ref": "#/$defs/selection"}, {"type": "null"}],
        },
        "preview": {
            "oneOf": [{"$ref": "#/$defs/preview"}, {"type": "null"}],
        },
    },
    "$defs": {
        "finiteNumber": {"type": "number"},
        "coordinate2": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": {"$ref": "#/$defs/finiteNumber"},
        },
        "coordinate3": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"$ref": "#/$defs/finiteNumber"},
        },
        "cell": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": {"type": "integer", "minimum": 0},
        },
        "path": {
            "type": "array",
            "minItems": 2,
            "maxItems": 256,
            "items": {"$ref": "#/$defs/cell"},
        },
        "area": {
            "oneOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "min", "max"],
                    "properties": {
                        "kind": {"const": "box"},
                        "min": {"$ref": "#/$defs/coordinate3"},
                        "max": {"$ref": "#/$defs/coordinate3"},
                    },
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "center", "radius", "height"],
                    "properties": {
                        "kind": {"const": "cylinder"},
                        "center": {"$ref": "#/$defs/coordinate3"},
                        "radius": {"type": "number", "exclusiveMinimum": 0},
                        "height": {"type": "number", "exclusiveMinimum": 0},
                    },
                },
            ]
        },
        "objective": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "label", "status", "position", "visible"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "label": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": ["active", "complete", "failed"]},
                "position": {"$ref": "#/$defs/coordinate3"},
                "visible": {"type": "boolean"},
            },
        },
        "selection": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "actor": {"type": "string"},
                "destination": {"$ref": "#/$defs/cell"},
                "path": {"$ref": "#/$defs/path"},
                "target": {"type": "string"},
                "aim_point": {"$ref": "#/$defs/coordinate3"},
                "area": {"$ref": "#/$defs/area"},
                "orientation": {"$ref": "#/$defs/coordinate2"},
                "declared_action": {"type": "string"},
            },
        },
        "preview": {
            "type": "object",
            "additionalProperties": False,
            "required": ["map_revision"],
            "properties": {
                "map_revision": {"type": "integer", "minimum": 0},
                "path_cost": {"type": ["number", "null"], "minimum": 0},
                "los": {"type": ["boolean", "null"]},
                "exposure": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                "crossed_zones": {
                    "type": "array",
                    "maxItems": 256,
                    "items": {"type": "string"},
                },
            },
        },
        "playerDisplay": {
            "type": "object",
            "additionalProperties": False,
            "required": ["label", "kind"],
            "properties": {
                "label": {"$ref": "#/$defs/playerText"},
                "kind": {"type": "string", "minLength": 1},
                "state": {"$ref": "#/$defs/playerText"},
                "stance": {"$ref": "#/$defs/playerText"},
                "side": {"$ref": "#/$defs/playerText"},
                "color": {"$ref": "#/$defs/playerText"},
                "icon": {"$ref": "#/$defs/playerText"},
                "interactive": {"type": "boolean"},
                "height_label": {"$ref": "#/$defs/playerText"},
            },
        },
        "playerText": {
            "type": "string",
            "minLength": 1,
            "pattern": r"^(?=\S(?:[\s\S]*\S)?$)(?![\s\S]*\x00)[\s\S]+$",
        },
    },
}


TACTICAL_GRID_SOURCE_EXAMPLE: dict[str, object] = {
    "scene": SPATIAL_SCENE_EXAMPLE,
    "objectives": [
        {
            "id": "reach-console",
            "label": "Reach the relay console",
            "status": "active",
            "position": [7, 5, 0],
            "visible": True,
        }
    ],
    "selection": {"actor": "keeper", "target": "bulkhead"},
    "preview": {
        "map_revision": 1,
        "path_cost": 3,
        "los": True,
        "exposure": 0.25,
        "crossed_zones": [],
    },
}


TACTICAL_GRID_SOURCE_NOTES: tuple[str, ...] = (
    (
        "scene must separately satisfy the complete spatial_scene definition. Every scene object's "
        "metadata.player, when present, must satisfy playerDisplay: label and kind are required; "
        "state, stance, side, color, icon, interactive and height_label are optional."
    ),
    (
        "Player display strings are unpadded and nonempty. label and the optional text fields are "
        "limited to 256 UTF-8 bytes; kind is limited to 64 UTF-8 bytes. Invalid player metadata "
        "hides that scene object from the player projection."
    ),
    (
        "Only objectives with visible true are projected. Objective IDs must be unique among "
        "projected objectives; labels are limited to 256 UTF-8 bytes. Position values and all "
        "preview numbers must be finite."
    ),
    (
        "Selection fields are inert presentation state until a declared panel action binds them. "
        "At action time actor and target must name visible entities, cells and points must fit the "
        "current map, orientation must be nonzero, declared_action must equal the action ID, and "
        "area extents must be valid."
    ),
    (
        "preview is advisory. Its map_revision must match the scene revision; stale "
        "or malformed values fall back to an empty non-authoritative preview. crossed_zones are "
        "deduplicated in their first-seen order."
    ),
    (
        "Runtime projection filters malformed objectives, selection, preview and metadata.player. "
        "The authoring definition describes their accepted structure. Contextual checks and the "
        "player-safe runtime fallback remain with the projection owner."
    ),
)


RESULT_CARD_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:sylvia-gm:content:result-card:1",
    "title": "Sylvia GM result-card declaration v1",
    "description": (
        "Ruleset-owned presentation attached to one tool declaration. Values resolve once from "
        "that action's admitted parameters and public result context, then remain historical."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["version"],
    "properties": {
        "version": {"type": "integer", "const": 1},
        "title": {"oneOf": [{"$ref": "#/$defs/text200"}, {"$ref": "#/$defs/valueReference"}]},
        "subtitle": {"$ref": "#/$defs/value"},
        "equation": {"$ref": "#/$defs/equation"},
        "equations": {"oneOf": [
            {"type": "array", "maxItems": 16, "items": {"$ref": "#/$defs/row"}},
            {"$ref": "#/$defs/rowsReference"},
        ]},
        "footprint": {"type": "string", "enum": ["inline", "compact"]},
        "details_label": {"$ref": "#/$defs/text200"},
        "summary": {
            "oneOf": [
                {"type": "array", "maxItems": 16, "items": {"$ref": "#/$defs/row"}},
                {"$ref": "#/$defs/rowsReference"},
            ],
        },
        "details": {
            "oneOf": [
                {"type": "array", "maxItems": 128, "items": {"$ref": "#/$defs/row"}},
                {"$ref": "#/$defs/rowsReference"},
            ],
        },
        "actions": {
            "type": "array",
            "maxItems": 8,
            "items": {"$ref": "#/$defs/action"},
        },
    },
    "$defs": {
        "equation": {
            "type": "object", "additionalProperties": False,
            "required": ["die_sides", "roll", "modifier", "total"],
            "properties": {
                "die_sides": {"oneOf": [
                    {"type": "integer", "minimum": 2, "maximum": 1000},
                    {"$ref": "#/$defs/valueReference"},
                ]},
                "roll": {"$ref": "#/$defs/value"},
                "modifier": {"$ref": "#/$defs/value"},
                "total": {"$ref": "#/$defs/value"},
                "rolls": {"oneOf": [
                    {"type": "array", "minItems": 1, "maxItems": 64,
                     "items": {"type": "integer", "minimum": 1, "maximum": 1000}},
                    {"$ref": "#/$defs/valueReference"},
                ]},
                "optional": {"type": "boolean", "default": False},
            },
        },
        "text200": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "pattern": r"[\s\S]*\S[\s\S]*",
        },
        "text4000": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4000,
            "pattern": r"[\s\S]*\S[\s\S]*",
        },
        "scalar": {
            "oneOf": [
                {"type": "string", "maxLength": 4000},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "null"},
            ]
        },
        "scalarList": {
            "type": "array",
            "maxItems": 64,
            "items": {"$ref": "#/$defs/scalar"},
        },
        "labels": {
            "type": "object",
            "maxProperties": 64,
            "additionalProperties": {"$ref": "#/$defs/text4000"},
        },
        "rowsReference": {
            "type": "object", "additionalProperties": False, "required": ["path"],
            "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 256}},
        },
        "valueReference": {
            "type": "object",
            "additionalProperties": False,
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "pattern": r"[\s\S]*\S[\s\S]*",
                },
                "labels": {"$ref": "#/$defs/labels"},
            },
        },
        "value": {
            "oneOf": [
                {"$ref": "#/$defs/scalar"},
                {"$ref": "#/$defs/scalarList"},
                {"$ref": "#/$defs/valueReference"},
            ]
        },
        "row": {
            "type": "object",
            "additionalProperties": False,
            "required": ["value"],
            "properties": {
                "label": {"$ref": "#/$defs/text200"},
                "value": {"$ref": "#/$defs/value"},
                "emphasis": {"type": "boolean", "default": False},
                "optional": {"type": "boolean", "default": False},
                "omit_zero": {"type": "boolean", "default": False},
                "equation": {"$ref": "#/$defs/equation"},
            },
        },
        "action": {
            "type": "object",
            "additionalProperties": False,
            "required": ["label", "tool", "parameters"],
            "properties": {
                "label": {"$ref": "#/$defs/text200"},
                "tool": {"type": "string", "minLength": 1, "maxLength": 128},
                "parameters": {
                    "type": "object",
                    "maxProperties": 64,
                    "propertyNames": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": r"[\s\S]*\S[\s\S]*",
                    },
                    "additionalProperties": {"$ref": "#/$defs/value"},
                },
            },
        },
    },
}


RESULT_CARD_EXAMPLE: dict[str, object] = {
    "version": 1,
    "title": "Relay check",
    "summary": [
        {
            "label": "Signal",
            "value": {
                "path": "context.signal_status",
                "labels": {"clear": "Clear", "broken": "Broken"},
            },
            "emphasis": True,
        }
    ],
    "details": [{"label": "Channel", "value": {"path": "parameters.channel"}}],
    "actions": [
        {
            "label": "Check again",
            "tool": "check_relay",
            "parameters": {"channel": {"path": "parameters.channel"}},
        }
    ],
}


RESULT_CARD_NOTES: tuple[str, ...] = (
    (
        "summary and details may be authored row lists or one public path to recorded rows "
        "(at most 16 summary or 128 detail rows). Recorded rows contain literal scalar/list "
        "values, never further value references. Labels, length limits and escaping are "
        "unchanged, and the resolved rows are saved with the result. A row may include a "
        "recorded equation for a named recipient or damage component; equations in referenced "
        "rows contain literal numbers, never new references."
    ),
    (
        "title may be text or a public value reference; subtitle is optional resolved text. "
        "An optional equation declares die_sides (a literal or recorded public value reference) "
        "plus recorded numeric roll, modifier and total "
        "values. The renderer does not calculate them. An equation marked optional is absent "
        "when its values are unavailable, such as an automatic outcome without a roll. "
        "Optional rolls holds one to 64 recorded die faces for a pool; roll remains its "
        "recorded subtotal and total remains authoritative, without frontend arithmetic. "
        "Optional equations declares at most 16 labelled equation rows shown initially; "
        "summary and details still select their own content independently."
    ),
    (
        "footprint selects inline or compact presentation; omitted footprint uses compact. "
        "details_label optionally names the closed detail expander. A row with omit_zero true "
        "is omitted only when its resolved value is numeric zero, not false or a string. "
        "These presentation choices and resolved values are saved with the result."
    ),
    (
        "At least one of title, resolved summary rows, resolved detail rows or actions must be "
        "nonempty. An empty or invalid declaration produces the generic operation card and never "
        "changes the mechanic's committed outcome."
    ),
    (
        "A value is a finite scalar, a list of at most 64 finite scalars, or a reference object. "
        "Direct and resolved strings are limited to 4,000 characters."
    ),
    (
        "Reference paths have at most 16 dot-separated segments, begin with parameters or context, "
        "and must resolve to an available public value. They cannot read state, private spatial "
        "evidence, source documents or arbitrary objects."
    ),
    (
        "When labels is present, the resolved scalar's string form must have a declared label. "
        "Boolean keys use lowercase true and false. Label text is limited to 4,000 characters."
    ),
    (
        "An optional row is omitted when its reference is unavailable or resolves to null. Other "
        "unavailable values invalidate the card. emphasis and optional default to false."
    ),
    (
        "Action tool names are at most 128 characters and contain only alphanumeric characters "
        "and underscores under Python's alphanumeric rules. Parameter names are nonblank text of "
        "at most 128 characters."
    ),
    (
        "Resolved cards contain saved values, not expressions. A button preserves the historical "
        "request, but the current action owner revalidates its tool and parameters when clicked."
    ),
)


__all__ = [
    "SPATIAL_SCENE_SCHEMA",
    "SPATIAL_SCENE_EXAMPLE",
    "SPATIAL_SCENE_NOTES",
    "TACTICAL_GRID_SOURCE_SCHEMA",
    "TACTICAL_GRID_SOURCE_EXAMPLE",
    "TACTICAL_GRID_SOURCE_NOTES",
    "RESULT_CARD_SCHEMA",
    "RESULT_CARD_EXAMPLE",
    "RESULT_CARD_NOTES",
]
