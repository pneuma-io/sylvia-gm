"""Check YAML's composed graph before aliases or merges can expand into Python values."""

from __future__ import annotations

from collections.abc import Iterable

from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode


class YamlShapeError(ValueError):
    """A source graph exceeds its caller's existing size or nesting limits."""


def validate_yaml_graph(
    root: Node,
    *,
    maximum_depth: int,
    maximum_nodes: int,
    maximum_object_members: int | None = None,
    count_mapping_keys: bool = True,
) -> None:
    """Count expanded alias occurrences without allocating or walking that expanded tree."""
    measured: dict[Node, tuple[int, int]] = {}
    active: set[Node] = set()

    def measure(node: Node, depth: int) -> tuple[int, int]:
        if depth > maximum_depth:
            raise YamlShapeError("YAML nesting exceeds the supported limit")
        if node in active:
            raise YamlShapeError("YAML contains a recursive alias")
        if node in measured:
            count, height = measured[node]
            if depth + height - 1 > maximum_depth:
                raise YamlShapeError("YAML nesting exceeds the supported limit")
            return count, height
        children: Iterable[Node] = ()
        if isinstance(node, MappingNode):
            if maximum_object_members is not None and len(node.value) > maximum_object_members:
                raise YamlShapeError("YAML object exceeds the supported member limit")
            if count_mapping_keys:
                children = (child for pair in node.value for child in pair)
            else:
                # Runtime's existing decoded-object limit counts each mapping and value.
                # Retain key type/depth checks before construction and alias expansion.
                for key, _value in node.value:
                    if not isinstance(key, ScalarNode) or key.tag not in {
                        "tag:yaml.org,2002:str",
                        "tag:yaml.org,2002:merge",
                        "tag:yaml.org,2002:value",
                    }:
                        raise YamlShapeError("YAML object keys must be text")
                    if depth + 1 > maximum_depth:
                        raise YamlShapeError("YAML nesting exceeds the supported limit")
                children = (value for _key, value in node.value)
        elif isinstance(node, SequenceNode):
            children = node.value
        active.add(node)
        count, height = 1, 1
        try:
            for child in children:
                child_count, child_height = measure(child, depth + 1)
                count += child_count
                height = max(height, child_height + 1)
                if count > maximum_nodes:
                    raise YamlShapeError("YAML expansion exceeds the supported node limit")
        finally:
            active.remove(node)
        measured[node] = count, height
        return count, height

    measure(root, 1)
