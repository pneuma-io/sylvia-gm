"""Seed one independently valid schema section inside the generator lifetime."""

from copy import copy, deepcopy

if __package__:
    from .srd_generator_tool_nodes import (
        _BUILTIN_RESOLVERS,
        _LOADER,
        _RESOLVE,
        _ROOT_HEADER,
        _SAFE_CONSTRUCTORS,
        _SAFE_MULTI_CONSTRUCTORS,
        _anchors,
        _mapping_keys,
        _nodes,
    )
else:
    from srd_generator_tool_nodes import (
        _BUILTIN_RESOLVERS,
        _LOADER,
        _RESOLVE,
        _ROOT_HEADER,
        _SAFE_CONSTRUCTORS,
        _SAFE_MULTI_CONSTRUCTORS,
        _anchors,
        _mapping_keys,
        _nodes,
    )


def seed_definition_schemas(entry, source, rendered):
    """Retain other nodes only when the exact schema section is self-contained."""
    if not isinstance(source, str) or not isinstance(rendered, str) or source == rendered:
        return False
    loader = None
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
        if any(
            marker in source or marker in rendered
            for marker in ("<<", "!!merge", "%TAG", "tag:yaml.org,2002:merge")
        ):
            raise ValueError("merge or tag-directive source requires ordinary loading")
        names = _mapping_keys(entry.node)
        old_headers = list(_ROOT_HEADER.finditer(source))
        new_headers = list(_ROOT_HEADER.finditer(rendered))
        if [match[1] for match in old_headers] != names or [
            match[1] for match in new_headers
        ] != names:
            raise ValueError("unchanged ordinary root sections required")
        position = names.index("definition_row_schemas")
        start = old_headers[position].start()
        end = old_headers[position + 1].start() if position + 1 < len(names) else len(source)
        new_start = new_headers[position].start()
        new_end = new_headers[position + 1].start() if position + 1 < len(names) else len(rendered)
        if source[:start] != rendered[:new_start] or source[end:] != rendered[new_end:]:
            raise ValueError("unchanged non-schema bytes required")
        if old_headers[position][0] != new_headers[position][0]:
            raise ValueError("unchanged schema header required")
        section = rendered[new_start:new_end]
        loader = _LOADER(section)
        fragment = loader.get_single_node()
        if _mapping_keys(fragment) != ["definition_row_schemas"]:
            raise ValueError("one ordinary schema section required")
        _mapping_keys(fragment.value[0][1])
        loader.construct_document(fragment)
        if not _anchors(section) <= _anchors(source[start:end]):
            raise ValueError("new document anchor requires ordinary loading")
        owned = {id(node) for node in _nodes(entry.node.value[position])}
        outside = [
            node
            for index, pair in enumerate(entry.node.value)
            if index != position
            for node in pair
        ]
        if any(id(node) in owned for node in _nodes(outside)):
            raise ValueError("incoming schema alias requires ordinary loading")
        updated = copy(entry.node)
        updated.value = entry.node.value[:]
        updated.value[position] = fragment.value[0]
        entry.text, entry.node = rendered, updated
        entry.value, entry.has_value = None, False
        return True
    except Exception:
        return False
    finally:
        if loader is not None:
            loader.dispose()
