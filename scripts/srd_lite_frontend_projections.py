"""Declare neutral panel values from existing SRD state and catalogue metadata."""

from copy import deepcopy

NAMESPACE = "sylvia.srd-lite.bg3"


def bind_frontend_projections(record, catalogue):
    """Keep rule-specific field names and category lookup in the package declaration."""
    categories = {
        profile["identity"]: profile["school"]
        for profile in catalogue["profiles"]
        if isinstance(profile.get("school"), str) and profile["school"].strip()
    }
    for surface in record["ui_surfaces"]:
        if surface["id"] not in {"dnd-party", "dnd-character", "dnd-encounter"}:
            continue
        fields = surface["source"]["fields"]
        fields["spell_categories"] = {"kind": "literal", "value": deepcopy(categories)}
        if surface["id"] == "dnd-encounter":
            fields["order_values"] = {
                "kind": "keyed_values",
                "entries": [
                    {
                        "key": {
                            "kind": "state",
                            "namespace": NAMESPACE,
                            "key": f"combat.initiative_{place}_actor",
                        },
                        "value": {
                            "kind": "state",
                            "namespace": NAMESPACE,
                            "key": f"combat.initiative_{place}_total",
                        },
                    }
                    for place in ("first", "second")
                ],
            }
