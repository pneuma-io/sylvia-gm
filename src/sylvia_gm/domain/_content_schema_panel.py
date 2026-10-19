"""Authoring definition for a Ruleset game panel and its declared sources and actions."""

from __future__ import annotations

from typing import Any

IDENTITY = {"type": "string", "minLength": 1, "pattern": r"^\S(?:[\s\S]*\S)?$(?![\s\S])"}
RENDERERS = [
    "party",
    "encounter",
    "inventory",
    "objectives",
    "unit_list",
    "key_value",
    "text",
    "tactical_grid",
    "coordinate_map",
    "network_graph",
    "piece_table",
]
PLACEMENTS = ["docked", "floating", "window"]
TACTICAL_BINDINGS = [
    "actor",
    "zone",
    "device",
    "destination",
    "path",
    "target",
    "aim_point",
    "area",
    "orientation",
    "declared_action",
]
NETWORK_BINDINGS = ["actor", "node", "link", "route_step", "remote_function", "declared_action"]

PANEL_DECLARATION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:sylvia-gm:content:panel-declaration:1",
    "title": "Compact Ruleset panel declaration",
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "capability", "renderer", "source"],
    "properties": {
        "id": {**IDENTITY, "description": "Unique panel identity within the package."},
        "capability": {**IDENTITY, "description": "A capability declared by the same package."},
        "renderer": {"enum": RENDERERS},
        "source": {"$ref": "#/$defs/source"},
        "title": {
            "type": "string",
            "minLength": 1,
            "maxLength": 256,
            "description": "Defaults to id in compact source.",
        },
        "icon": {"type": "string", "minLength": 1, "maxLength": 256, "default": "◇"},
        "group": {"oneOf": [{"type": "null"}, IDENTITY], "default": None},
        "order": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10000,
            "description": "Defaults to the surface's zero-based source-list position.",
        },
        "default_size": {"type": "integer", "minimum": 96, "maximum": 2000, "default": 180},
        "minimum_size": {"type": "integer", "minimum": 96, "maximum": 2000, "default": 96},
        "default_placement": {"enum": PLACEMENTS, "default": "docked"},
        "allowed_placements": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "uniqueItems": True,
            "items": {"enum": PLACEMENTS},
            "default": ["docked", "window"],
        },
        "actions": {
            "type": "array",
            "maxItems": 16,
            "items": {"$ref": "#/$defs/action"},
            "default": [],
        },
    },
    "$defs": {
        "source": {
            "oneOf": [
                {"$ref": "#/$defs/state_source"},
                {"$ref": "#/$defs/literal_source"},
                {"$ref": "#/$defs/participant_source"},
                {"$ref": "#/$defs/campaign_plan_source"},
                {"$ref": "#/$defs/object_source"},
            ]
        },
        "literal_source": {"type":"object","additionalProperties":False,"required":["kind","value"],"properties":{"kind":{"const":"literal"},"value":{"type":"object"}}},
        "state_source": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "namespace", "key"],
            "properties": {"kind": {"const": "state"}, "namespace": IDENTITY, "key": IDENTITY},
        },
        "participant_source": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "kinds"],
            "properties": {
                "kind": {"const": "participants"},
                "kinds": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 16,
                    "uniqueItems": True,
                    "items": IDENTITY,
                },
            },
        },
        "campaign_plan_source": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "path"],
            "properties": {
                "kind": {"const": "campaign_plan"},
                "path": {"type": "array", "minItems": 1, "maxItems": 16, "items": IDENTITY},
            },
        },
        "object_source": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "fields"],
            "properties": {
                "kind": {"const": "object"},
                "fields": {
                    "type": "object",
                    "minProperties": 1,
                    "maxProperties": 16,
                    "propertyNames": IDENTITY,
                    "additionalProperties": {"$ref": "#/$defs/source"},
                },
            },
        },
        "action": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "label", "intent", "params"],
            "properties": {
                "id": IDENTITY,
                "label": {"type": "string", "minLength": 1, "maxLength": 256},
                "intent": {**IDENTITY, "description": "An exact public tool name in this package."},
                "params": {
                    "type": "object",
                    "description": "Open fixed Ruleset parameter data.",
                    "additionalProperties": True,
                },
                "bindings": {
                    "type": "object",
                    "minProperties": 1,
                    "maxProperties": 16,
                    "propertyNames": IDENTITY,
                    "additionalProperties": {
                        "enum": sorted(set(["actor", "piece"] + TACTICAL_BINDINGS + NETWORK_BINDINGS))
                    },
                },
                "spatial_preview": {"enum": ["segment"]},
                "target_reference": {
                    "type": "string", "minLength": 1, "maxLength": 64,
                    "pattern": "^[A-Za-z_][A-Za-z0-9_]*$",
                    "description": "Coordinate target binding uses the map's admitted mechanical reference."
                },
                "actor_reference": {
                    "type": "string", "minLength": 1, "maxLength": 64,
                    "pattern": "^[A-Za-z_][A-Za-z0-9_]*$",
                    "description": "Coordinate map actor binding reads this entity metadata field."
                },
            },
        },
    },
    "allOf": [
        {
            "if": {"properties": {"renderer": {"const": "piece_table"}}},
            "then": {"properties": {"actions": {"items": {"properties": {
                "bindings": {"additionalProperties": {"enum": ["piece"]}}
            }}}}},
        },
        {
            "if": {"properties": {"renderer": {"const": "coordinate_map"}}},
            "then": {
                "properties": {
                    "actions": {
                        "items": {
                            "properties": {
                                "bindings": {"additionalProperties": {
                                    "enum": ["actor", "target", "aim_point", "declared_action"]
                                }}
                            }
                        }
                    }
                }
            },
        },
        {
            "if": {"properties": {"renderer": {"enum": ["party", "unit_list"]}}},
            "then": {
                "properties": {
                    "actions": {
                        "items": {
                            "properties": {
                                "bindings": {"additionalProperties": {"enum": ["actor"]}}
                            }
                        }
                    }
                }
            },
        },
        {
            "if": {"properties": {"renderer": {"const": "tactical_grid"}}},
            "then": {
                "properties": {
                    "actions": {
                        "items": {
                            "properties": {
                                "bindings": {"additionalProperties": {"enum": TACTICAL_BINDINGS}}
                            }
                        }
                    }
                }
            },
        },
        {
            "if": {"properties": {"renderer": {"const": "network_graph"}}},
            "then": {
                "properties": {
                    "actions": {
                        "items": {
                            "properties": {
                                "bindings": {"additionalProperties": {"enum": NETWORK_BINDINGS}}
                            }
                        }
                    }
                }
            },
        },
        {
            "if": {
                "properties": {
                    "renderer": {"not": {"enum": ["party", "unit_list", "inventory", "tactical_grid", "coordinate_map", "network_graph", "piece_table"]}}
                }
            },
            "then": {"properties": {"actions": {"items": {"not": {"required": ["bindings"]}}}}},
        },
    ],
}

EXAMPLE = {
    "id": "party",
    "capability": "example.rules.party",
    "renderer": "party",
    "source": {"kind": "participants", "kinds": ["player", "companion"]},
}

FORMAT_NOTES = (
    (
        "The compact compiler supplies omitted display, size, placement, order, and "
        "actions fields before package validation."
    ),
    (
        "minimum_size must not exceed default_size, and default_placement must occur in "
        "allowed_placements."
    ),
    "Capability and action intent references require package-level checks.",
    "Binding keys must not overlap params keys; binding sources must be unique within an action.",
    "Object source nesting is limited to depth 3 by the package validator.",
    "Small-label validation also rejects unsafe text; its byte-level rule remains procedural here.",
)

__all__ = ["PANEL_DECLARATION_SCHEMA", "EXAMPLE", "FORMAT_NOTES"]
