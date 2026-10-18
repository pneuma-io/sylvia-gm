"""Serialize established runtime defaults once without changing executable tools."""

import yaml


def restore_tool_defaults(runtime):
    """Present emitters with the same explicit defaults used by the neutral interpreter."""
    for tool in runtime.get("tools", {}).values():
        tool.setdefault("pre_conditions", [])
        for parameter in tool.get("parameters", []) or []:
            parameter.setdefault("required", True)
            parameter.setdefault("type", "string")
    return runtime


def compact_runtime_defaults(text):
    """Omit only standalone declarations of established tool and parameter defaults."""
    document = yaml.compose(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = next(value for key, value in document.value if key.value == "tools")
    spans = set()

    def omit_line(key, value):
        """Keep inline entries, anchors and aliases; omit only one complete generated line."""
        start = text.rfind("\n", 0, key.start_mark.index) + 1
        end = text.find("\n", key.start_mark.index)
        if end == -1:
            end = len(text)
        if value.end_mark.index > end or value.end_mark.index < key.start_mark.index:
            return
        if text[start : key.start_mark.index].strip() or text[value.end_mark.index : end].strip():
            return
        if "&" in text[start:end] or "*" in text[start:end]:
            return
        spans.add((start, min(end + 1, len(text))))

    def compact_parameter(parameter):
        if not isinstance(parameter, yaml.MappingNode):
            return
        for name, setting in parameter.value:
            if name.value == "default" and setting.tag == "tag:yaml.org,2002:null":
                omit_line(name, setting)

    declarations = next(
        (value for key, value in document.value if key.value == "parameter_declarations"), None
    )
    if isinstance(declarations, yaml.MappingNode):
        for _, parameter in declarations.value:
            compact_parameter(parameter)

    for _, tool in tools.value:
        for key, value in tool.value:
            if (
                key.value in {"pre_conditions", "parameters"}
                and isinstance(value, yaml.SequenceNode)
                and not value.value
            ):
                omit_line(key, value)
            elif key.value == "parameters" and isinstance(value, yaml.SequenceNode):
                for parameter in value.value:
                    compact_parameter(parameter)
                    if not isinstance(parameter, yaml.MappingNode):
                        continue
                    for name, setting in parameter.value:
                        if (
                            name.value == "required"
                            and setting.tag == "tag:yaml.org,2002:bool"
                            and setting.value.lower() in {"true", "yes", "on"}
                        ) or (
                            name.value == "type"
                            and setting.tag == "tag:yaml.org,2002:str"
                            and setting.value == "string"
                        ):
                            omit_line(name, setting)
    parts = []
    previous = 0
    for start, end in sorted(spans):
        if start < previous:
            continue
        parts.append(text[previous:start])
        previous = end
    parts.append(text[previous:])
    return "".join(parts)
