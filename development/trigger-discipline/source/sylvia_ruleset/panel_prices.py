"""Preserve the accepted native-cost previews in newly generated TD packages."""


def _nodes(effects):
    for node in effects:
        yield node
        for key in ("on_true", "on_false"):
            yield from _nodes(node.get(key, []))
        if "for_each" in node:
            yield from _nodes(node["for_each"].get("do", []))


def apply(tools):
    """Read each existing debit expression; change presentation metadata only.

    This ports the accepted panel candidate's calculation rules into its generator.
    Execution still owns costs; no price table or numerical defaults are introduced.
    """
    for name, tool in tools.items():
        if name in {"reload_weapon", "recover_recoil"}:
            tool["metadata"].setdefault("player_controls", {})
        policy = tool.get("metadata", {}).get("player_controls")
        if policy is None:
            continue
        debits = [node["decrement"]["amount"] for node in _nodes(tool["effects"])
                  if "decrement" in node and
                  str(node["decrement"]["field"]).endswith(".ap")]
        if not debits:
            continue
        if len(set(map(str, debits))) != 1:
            raise ValueError(f"{name}: a single displayed AP cost is not defined")
        expression = str(debits[0])
        assignments = [node["set"]["value"] for node in _nodes(tool["effects"])
                       if node.get("set", {}).get("field") == expression and
                       isinstance(node["set"]["value"], str)]
        if expression == "_fire_ap":
            if len(assignments) != 1:
                raise ValueError(f"{name}: firing price assignment is ambiguous")
            expression = assignments[0]
        elif expression == "_function.ap":
            assignments = [node["set"]["value"] for node in _nodes(tool["effects"])
                           if node.get("set", {}).get("field") == "_function" and
                           isinstance(node["set"]["value"], str)]
            if len(assignments) != 1:
                raise ValueError(f"{name}: remote function price is ambiguous")
            expression = assignments[0] + ".ap"
        calculations = policy.setdefault("calculations", [])
        field = "_preview_ap_cost"
        alternatives = []
        if expression == "_move_ap":
            field = expression
        elif name in {"intrude_network", "pivot_network"}:
            assignments = [node["set"]["value"] for node in _nodes(tool["effects"])
                           if node.get("set", {}).get("field") == "_rule" and
                           isinstance(node["set"]["value"], str)]
            if len(assignments) != 2:
                raise ValueError(f"{name}: intrusion/reentry prices are ambiguous")
            calculations.extend([
                {"condition": "method == 'INTRUDE'",
                 "set": {"field": field, "value": assignments[0] + ".ap"}},
                {"condition": "method != 'INTRUDE'",
                 "set": {"field": "_preview_reentry_ap_cost", "value": assignments[1] + ".ap"}},
            ])
            alternatives.append("_preview_reentry_ap_cost")
        elif name == "network_connection":
            calculations.extend([
                {"condition": "action == 'CONNECT'", "set": {"field": field, "value": expression}},
                {"condition": "action == 'DISCONNECT'",
                 "set": {"field": "_preview_disconnect_ap_cost", "value": "0"}},
            ])
            alternatives.append("_preview_disconnect_ap_cost")
        else:
            calculations.append({"set": {"field": field, "value": expression}})
        if not policy.get("checks") and name in {"reload_weapon", "recover_recoil"}:
            policy["actor_parameter"] = "actor"
            policy["checks"] = [
                {"condition": "actor.side == active_side",
                 "reason": "This character cannot act during the other team's turn."},
                {"condition": f"actor.ap >= {field}",
                 "reason": "This character does not have enough action points."},
            ]
        preview = policy.setdefault("preview", {
            "version": 1, "title": "Action preview", "summary": [],
        })
        preview["summary"] = [row for row in preview["summary"]
                              if row.get("label") not in {"AP cost", "Estimated AP"}]
        for price_field in (field, *alternatives):
            row = {"label": "AP cost", "value": {"path": "context." + price_field}}
            if alternatives or field == "_move_ap":
                row["optional"] = True
            preview["summary"].append(row)
        for row in preview["summary"]:
            if row.get("label") == "Actor":
                row["label"] = "Character"
