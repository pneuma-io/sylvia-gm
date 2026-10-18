"""Reuse one safe-YAML document within an explicit generator lifetime."""

from contextvars import ContextVar
from copy import deepcopy
from functools import wraps

import yaml

_SAFE_CONSTRUCTORS = dict(yaml.constructor.SafeConstructor.yaml_constructors)
_SAFE_MULTI_CONSTRUCTORS = dict(yaml.constructor.SafeConstructor.yaml_multi_constructors)


class _ComposedNodeReuse:
    """Keep a private built-in value and detach every emitter's mutable graph."""

    def __init__(self):
        self.clear()

    def clear(self):
        self.text = None
        self.node = None
        self.loader_type = None
        self.value = None
        self.has_value = False
        self.resolver_config = None

    def load(self, text, loader_type):
        loader = None
        try:
            resolver_config = deepcopy(
                (loader_type.yaml_implicit_resolvers, loader_type.yaml_path_resolvers)
            )
            if self.resolver_config != resolver_config:
                self.clear()
            hit = self.text == text and self.loader_type is loader_type
            builtin = (
                loader_type.yaml_constructors == _SAFE_CONSTRUCTORS
                and loader_type.yaml_multi_constructors == _SAFE_MULTI_CONSTRUCTORS
            )
            if hit and builtin and self.has_value:
                return deepcopy(self.value)
            loader = loader_type("" if hit else text)
            node = self.node if hit else loader.get_single_node()
            value = loader.construct_document(node) if node is not None else None
            self.text, self.node, self.loader_type = text, node, loader_type
            self.value = value if builtin else None
            self.has_value = builtin
            self.resolver_config = resolver_config
            return deepcopy(value) if builtin else value
        except BaseException:
            self.clear()
            raise
        finally:
            if loader is not None:
                loader.dispose()


_active_reuse = ContextVar("srd_generator_yaml_reuse", default=None)


def seed_generator_tool_fragments(fragments, rendered):
    """Seed proven tool splices only in the existing explicit generator scope."""
    entry = _active_reuse.get()
    if entry is None:
        return False
    if __package__:
        from .srd_generator_tool_nodes import seed_tool_fragments
    else:
        from srd_generator_tool_nodes import seed_tool_fragments
    return seed_tool_fragments(entry, fragments, rendered)


def seed_generator_definition_schemas(source, rendered):
    """Seed proven schema-only edits within the existing generator scope."""
    entry = _active_reuse.get()
    if entry is None:
        return False
    if __package__:
        from .srd_generator_schema_nodes import seed_definition_schemas
    else:
        from srd_generator_schema_nodes import seed_definition_schemas
    return seed_definition_schemas(entry, source, rendered)


def clear_generator_yaml_reuse():
    """Release the current entry when construction or later restoration fails."""
    entry = _active_reuse.get()
    if entry is not None:
        entry.clear()


def load_generator_yaml(text):
    """Return fresh graphs; custom safe constructors still run on every read."""
    loader_type = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    entry = _active_reuse.get()
    if entry is None or not isinstance(text, str):
        return yaml.load(text, Loader=loader_type)
    if entry.text is not None and entry.text != text:
        try:
            seed_generator_definition_schemas(entry.text, text)
        except BaseException:
            entry.clear()
            raise
    return entry.load(text, loader_type)


def scoped_generator_yaml(function):
    """Bound reuse to one generator call and release it on success or any failure."""

    @wraps(function)
    def run(*args, **kwargs):
        entry = _ComposedNodeReuse()
        token = _active_reuse.set(entry)
        try:
            return function(*args, **kwargs)
        finally:
            entry.clear()
            _active_reuse.reset(token)

    return run
