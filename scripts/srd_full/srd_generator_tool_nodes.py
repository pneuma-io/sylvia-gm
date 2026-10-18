"""Seed conservative tool-only node splices inside the spell generator lifetime."""

import re
from copy import copy, deepcopy

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.resolver import BaseResolver, Resolver
from yaml.tokens import AnchorToken

_LOADER = getattr(getattr(yaml, "cyaml", None), "CSafeLoader", None)
_SAFE_CONSTRUCTORS = dict(yaml.constructor.SafeConstructor.yaml_constructors)
_SAFE_MULTI_CONSTRUCTORS = dict(yaml.constructor.SafeConstructor.yaml_multi_constructors)
_RESOLVE = BaseResolver.resolve
_BUILTIN_RESOLVERS = deepcopy((Resolver.yaml_implicit_resolvers, {}))
_MAP = "tag:yaml.org,2002:map"
_STRING = "tag:yaml.org,2002:str"
_ROOT_HEADER = re.compile(r"(?m)^([A-Za-z_]\w*):[^\n]*\n")
_TOOL_HEADER = re.compile(r"(?m)^  ([A-Za-z_]\w*):[^\n]*\n")


def _nodes(roots):
    """Visit each composed node once, including recursive aliases."""
    seen, pending = set(), list(roots)
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        yield node
        if isinstance(node, MappingNode):
            for key, value in node.value:
                pending.extend((key, value))
        elif isinstance(node, SequenceNode):
            pending.extend(node.value)


def _mapping_keys(node):
    if not isinstance(node, MappingNode) or node.tag != _MAP:
        raise ValueError("ordinary mapping required")
    keys = []
    for key, _ in node.value:
        if not isinstance(key, ScalarNode) or key.tag != _STRING:
            raise ValueError("ordinary string keys required")
        keys.append(key.value)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate keys require ordinary loading")
    return keys


def _anchors(text):
    return {
        token.value for token in yaml.scan(text, Loader=_LOADER) if isinstance(token, AnchorToken)
    }


def _fragment(text):
    """Compose and validate one independent fragment using the unmodified safe loader."""
    loader = _LOADER(text)
    try:
        node = loader.get_single_node()
        keys = _mapping_keys(node)
        if len(keys) != 1 or len(list(_TOOL_HEADER.finditer(text))) != 1:
            raise ValueError("one ordinary tool fragment required")
        # Successful original construction rejects unsafe tags and malformed special values.
        loader.construct_document(node)
        return keys[0], node.value[0], _anchors(text)
    finally:
        loader.dispose()


def seed_tool_fragments(entry, fragments, rendered):
    """Retain unchanged nodes only for exact, independently valid local tool edits."""
    source = fragments._text
    if source == rendered:
        return False
    try:
        if entry.text != source or entry.node is None or entry.loader_type is not _LOADER:
            raise ValueError("exact cached input required")
        current = deepcopy((_LOADER.yaml_implicit_resolvers, _LOADER.yaml_path_resolvers))
        if entry.resolver_config != current or _LOADER.resolve is not _RESOLVE:
            raise ValueError("unchanged resolver configuration required")
        if current != _BUILTIN_RESOLVERS:
            raise ValueError("built-in resolver configuration required")
        if (
            _LOADER.yaml_constructors != _SAFE_CONSTRUCTORS
            or _LOADER.yaml_multi_constructors != _SAFE_MULTI_CONSTRUCTORS
        ):
            raise ValueError("built-in constructors required")
        # Cached construction may flatten merge nodes. Reject their source syntax too.
        if any(
            marker in source or marker in rendered
            for marker in ("<<", "!!merge", "%TAG", "tag:yaml.org,2002:merge")
        ):
            raise ValueError("merge or tag-directive source requires ordinary loading")
        root_keys = _mapping_keys(entry.node)
        headers = list(_ROOT_HEADER.finditer(source))
        if [match[1] for match in headers] != root_keys or "tools" not in root_keys:
            raise ValueError("ordinary unchanged root sections required")
        position = root_keys.index("tools")
        if position + 1 == len(headers):
            raise ValueError("bounded tools section required")
        start, end = headers[position].end(), headers[position + 1].start()
        tools = entry.node.value[position][1]
        names = _mapping_keys(tools)
        tool_headers = list(_TOOL_HEADER.finditer(source, start, end))
        if [match[1] for match in tool_headers] != names:
            raise ValueError("exact ordinary tool headers required")
        bounds = {
            name: (
                match.start(),
                tool_headers[index + 1].start() if index + 1 < len(tool_headers) else end,
            )
            for index, (name, match) in enumerate(zip(names, tool_headers, strict=True))
        }
        replacements, additions, changed = {}, [], set()
        edits = list(fragments._replacements)
        for left, right, text in edits:
            name, pair, anchors = _fragment(text)
            if bounds.get(name) != (left, right) or name in changed:
                raise ValueError("exact nonoverlapping tool replacement required")
            if not anchors <= _anchors(source[left:right]):
                raise ValueError("new document anchor requires ordinary loading")
            replacements[name] = pair
            changed.add(name)
        if fragments._insertions:
            if fragments._insertion != end or headers[position + 1][1] != "procedures":
                raise ValueError("exact tool insertion boundary required")
            for text in fragments._insertions:
                name, pair, anchors = _fragment(text)
                if name in names or name in changed or anchors:
                    raise ValueError("unique unanchored new tool required")
                additions.append(pair)
                changed.add(name)
            edits.append((end, end, "".join(fragments._insertions)))
        if not changed:
            raise ValueError("no attested tool changes")
        cursor, pieces = 0, []
        for left, right, text in sorted(edits):
            if left < cursor:
                raise ValueError("overlapping source edits")
            pieces.extend((source[cursor:left], text))
            cursor = right
        pieces.append(source[cursor:])
        if "".join(pieces) != rendered:
            raise ValueError("unchanged non-tool bytes required")
        owned = [
            node
            for name, pair in zip(names, tools.value, strict=True)
            if name in replacements
            for node in pair
        ]
        owned_ids = {id(node) for node in _nodes(owned)}
        outside = [
            node
            for index, pair in enumerate(entry.node.value)
            if index != position
            for node in pair
        ]
        outside += [
            node
            for name, pair in zip(names, tools.value, strict=True)
            if name not in replacements
            for node in pair
        ]
        if any(id(node) in owned_ids for node in _nodes(outside)):
            raise ValueError("incoming cross-tool alias requires ordinary loading")
        next_tools = copy(tools)
        next_tools.value = [
            replacements.get(name, pair) for name, pair in zip(names, tools.value, strict=True)
        ] + additions
        next_root = copy(entry.node)
        next_root.value = entry.node.value[:]
        next_root.value[position] = (entry.node.value[position][0], next_tools)
        entry.text, entry.node = rendered, next_root
        entry.value, entry.has_value = None, False
        return True
    except Exception:
        # Keep the previous entry; the real next load performs ordinary parsing/errors.
        return False
