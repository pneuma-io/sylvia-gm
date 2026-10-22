"""Share profitable exact parameter declarations after final package composition."""

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy

import yaml

POOL = "parameter_declarations"
MARKER = "# Shared generated parameter declarations; expand before source regeneration."


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def _document(text):
    return yaml.compose(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))


def _data(text):
    return yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))


def _resolve(data):
    from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations

    return resolve_parameter_declarations(data)


def _field(mapping, name):
    return next((value for key, value in mapping.value if key.value == name), None)


def _parameters(document):
    tools = _field(document, "tools")
    if tools is None:
        return
    for name, tool in tools.value:
        parameters = _field(tool, "parameters")
        if parameters is not None:
            yield name.value, parameters


def _nodes(node):
    if isinstance(node, yaml.MappingNode):
        return 1 + sum(_nodes(key) + _nodes(value) for key, value in node.value)
    if isinstance(node, yaml.SequenceNode):
        return 1 + sum(_nodes(value) for value in node.value)
    return 1


def _end(text, index):
    """A node's end may include the indentation of the next sibling; retain that line."""
    start = text.rfind("\n", 0, index) + 1
    return start if not text[start:index].strip() else index


def _apply(text, edits):
    """Apply nonoverlapping original-source marks without altering any other byte."""
    parts, previous = [], 0
    for start, end, replacement in sorted(edits):
        if start < previous:
            raise ValueError("Parameter declaration source spans overlap")
        parts.extend((text[previous:start], replacement))
        previous = end
    parts.append(text[previous:])
    return "".join(parts)


def _dump(value, *, flow=False):
    return yaml.dump(
        value,
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=flow,
        width=1_000_000 if flow else 96,
    ).rstrip("\n")


def _scalar_spans(node):
    if isinstance(node, yaml.ScalarNode):
        yield node.start_mark.index, node.end_mark.index
    elif isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            yield from _scalar_spans(key)
            yield from _scalar_spans(value)
    else:
        for value in node.value:
            yield from _scalar_spans(value)


def _comments(text, node, start, end):
    """Retain comments from a removed pool, excluding hashes inside literal scalar values."""
    spans = sorted(_scalar_spans(node))
    comments, cursor = [], 0
    for match in re.finditer("#", text[start:end]):
        index = start + match.start()
        while cursor < len(spans) and spans[cursor][1] <= index:
            cursor += 1
        if cursor < len(spans) and spans[cursor][0] <= index < spans[cursor][1]:
            continue
        line_end = text.find("\n", index, end)
        line_end = end if line_end == -1 else line_end
        # Once a comment starts, every later hash on that line is part of the same comment.
        if comments and comments[-1][0] == line_end:
            continue
        comments.append((line_end, text[index:line_end] + "\n"))
    return "".join(value for _, value in comments)


def restore_runtime(text):
    """Expand actual parameter IDs before every text emitter, preserving section markers."""
    document = _document(text)
    pool = _field(document, POOL)
    if pool is None:
        return text
    original = _data(text)
    normalized = _resolve(original)
    edits, seen = [], set()
    for name, parameters in _parameters(document):
        for index, node in enumerate(parameters.value):
            if not isinstance(node, yaml.ScalarNode):
                continue
            span = (node.start_mark.index, node.end_mark.index)
            if span in seen:
                continue
            seen.add(span)
            descriptor = normalized["tools"][name]["parameters"][index]
            raw = text[span[0] : span[1]]
            anchor = re.match(r"&([A-Za-z0-9_-]+)\s+", raw)
            line_start = text.rfind("\n", 0, span[0]) + 1
            prefix = text[line_start : span[0]]
            if parameters.flow_style or anchor or not re.fullmatch(r"[ \t]*- ", prefix):
                replacement = (anchor.group(0) if anchor else "") + _dump(descriptor, flow=True)
            else:
                lines = _dump(descriptor).splitlines()
                indent = " " * node.start_mark.column
                replacement = lines[0] + "".join("\n" + indent + line for line in lines[1:])
            edits.append((*span, replacement))
    key = next(key for key, value in document.value if value is pool)
    start = text.rfind("\n", 0, key.start_mark.index) + 1
    end = _end(text, pool.end_mark.index)
    previous_line = text.rfind("\n", 0, max(0, start - 1)) + 1
    if text[previous_line:start].strip() == MARKER:
        start = previous_line
    edits.append((start, end, _comments(text, pool, key.start_mark.index, end)))
    restored = _apply(text, edits)
    if _data(restored) != normalized:
        raise ValueError("Expanded parameter declarations changed the normalized runtime")
    return restored


def _descriptor_key(descriptor):
    return json.dumps(descriptor, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _identifier(descriptor, key):
    name = re.sub(r"[^a-z0-9_]+", "_", descriptor["name"].lower()).strip("_")
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return "param_" + (name or "value") + "_" + digest


def compact_runtime(text):
    """Pool only profitable equal literal descriptors, retaining all effects and tool schemas."""
    text = restore_runtime(text)
    original = _data(text)
    document = _document(text)
    parameter_rows = list(_parameters(document))
    occurrences = Counter(id(node) for _, sequence in parameter_rows for node in sequence.value)
    sequence_occurrences = Counter(id(sequence) for _, sequence in parameter_rows)
    groups = {}
    for name, sequence in parameter_rows:
        if sequence.flow_style or sequence_occurrences[id(sequence)] != 1:
            continue
        for index, node in enumerate(sequence.value):
            if not isinstance(node, yaml.MappingNode) or node.flow_style:
                continue
            if occurrences[id(node)] != 1:
                continue
            start = text.rfind("\n", 0, node.start_mark.index) + 1
            prefix = text[start : node.start_mark.index]
            if not re.fullmatch(r"[ \t]*- ", prefix):
                continue
            end = _end(text, node.end_mark.index)
            # Authored comments and anchors remain in place; they need no new encoding.
            if any(character in text[start:end] for character in "#&*"):
                continue
            descriptor = original["tools"][name]["parameters"][index]
            key = _descriptor_key(descriptor)
            row = groups.setdefault(
                key, {"descriptor": descriptor, "nodes": _nodes(node), "uses": []}
            )
            row["uses"].append((start, end, prefix[:-2]))
    pool, edits, savings = {}, [], 0
    for key, row in sorted(groups.items()):
        saved = len(row["uses"]) * (row["nodes"] - 1) - row["nodes"] - 1
        if saved <= 0:
            continue
        identifier = _identifier(row["descriptor"], key)
        if identifier in pool and pool[identifier] != row["descriptor"]:
            raise ValueError("Parameter declaration identifier collision")
        pool[identifier] = deepcopy(row["descriptor"])
        for start, end, indent in row["uses"]:
            edits.append((start, end, indent + "- " + identifier + "\n"))
        savings += saved
    if savings <= 2:
        return text
    shared = _apply(text, edits)
    if shared and not shared.endswith("\n"):
        shared += "\n"
    shared += MARKER + "\n" + _dump({POOL: pool}) + "\n"
    if _resolve(_data(shared)) != original:
        raise ValueError("Shared parameter declarations changed the normalized runtime")
    return shared
