"""Scoped tool splices preserve safe loader behavior and fresh graph ownership."""

import re

import pytest
import yaml
from scripts import srd_actor_spell_modifiers as actor
from scripts import srd_generator_yaml as generator
from scripts import srd_timed_actor_defenses as defense
from scripts import srd_timed_senses as senses
from scripts.srd_full.srd_runtime_fragments import _ToolFragments

from tests.srd_full.test_srd_generator_yaml import _same_graph


def _tool(title="Actual changed fragment"):
    return defense._tool(
        title,
        [defense._parameter("actor_state", resolve="entity")],
        [defense._set("_actual_count", 123)],
    )


def _document():
    return (
        "version: 1\ndefinitions:\n  numeric: 123\ntools:\n"
        "  sample:\n    description: Old\n    parameters: []\n    effects: []\n"
        "  untouched:\n    description: Retained\n    parameters: []\n    effects: []\n"
        "procedures: {}\ndefinition_row_schemas: {}\n"
    )


@pytest.fixture
def entry():
    current = generator._ComposedNodeReuse()
    token = generator._active_reuse.set(current)
    try:
        yield current
    finally:
        current.clear()
        generator._active_reuse.reset(token)


def _same(text, current):
    # The ordinary path still performs the source-owned declaration/default restoration.
    token = generator._active_reuse.set(None)
    try:
        ordinary = defense._load_runtime(text)
    finally:
        generator._active_reuse.reset(token)
    actual = defense._load_runtime(text)
    _same_graph(ordinary, actual)
    return actual


@pytest.mark.parametrize("emit", [actor._emit, senses._replace_tools], ids=["actor", "senses"])
def test_actual_multi_tool_emitter_does_not_recompose_changed_whole_document(
    entry, monkeypatch, emit
):
    source = _document()
    inputs = []
    original = yaml.CSafeLoader.__init__

    def observed(self, stream):
        inputs.append(stream)
        original(self, stream)

    monkeypatch.setattr(yaml.CSafeLoader, "__init__", observed)
    before = defense._load_runtime(source)
    rendered = emit(source, {"sample": _tool(), "new_tool": _tool("New tool")})
    actual = defense._load_runtime(rendered)
    assert rendered not in inputs, "the changed whole document was parsed again"
    assert source in inputs and entry.text == rendered
    assert list(actual["tools"]) == ["sample", "untouched", "new_tool"]
    assert actual["tools"]["sample"]["description"] == "Actual changed fragment"
    assert actual["tools"]["untouched"] is not before["tools"]["untouched"]
    inputs.clear()
    _same(rendered, entry)
    actual["tools"]["untouched"]["description"] = "Private local mutation"
    assert defense._load_runtime(rendered)["tools"]["untouched"]["description"] == "Retained"


@pytest.mark.parametrize("emit", [actor._emit, senses._replace_tools], ids=["actor", "senses"])
def test_changed_d20_anchor_incoming_alias_uses_ordinary_parse_and_new_shared_card(entry, emit):
    source = (
        "version: 1\ntools:\n"
        "  d20_test:\n    parameters: []\n    effects: []\n    metadata:\n"
        "      result_card: &d20_result_card\n        title: Old\n"
        "  untouched:\n    parameters: []\n    effects: []\n    metadata:\n"
        "      result_card: *d20_result_card\nprocedures: {}\n"
    )
    before = defense._load_runtime(source)
    assert (
        before["tools"]["d20_test"]["metadata"]["result_card"]
        is before["tools"]["untouched"]["metadata"]["result_card"]
    )
    tool = _tool()
    tool["metadata"]["result_card"] = {"title": "New shared card"}
    rendered = emit(source, {"d20_test": tool})
    assert entry.text == source
    actual = _same(rendered, entry)
    first = actual["tools"]["d20_test"]["metadata"]["result_card"]
    assert first is actual["tools"]["untouched"]["metadata"]["result_card"]
    assert first["title"] == "New shared card"


def test_merge_source_falls_back_after_cache_constructor_flattens_nodes(entry):
    source = (
        "version: 1\ndefinitions:\n"
        "  base: &base {description: Merged, effects: [], parameters: []}\n"
        "tools:\n  sample:\n    <<: *base\nprocedures: {}\n"
    )
    defense._load_runtime(source)
    rendered = senses._replace_tools(source, {"sample": _tool()})
    assert entry.text == source
    _same(rendered, entry)


def test_external_fragment_alias_falls_back_and_keeps_actual_shared_identity(entry):
    source = _document().replace(
        "  numeric: 123", "  numeric: 123\n  actual_shared: &shared {notes: [one]}"
    )
    defense._load_runtime(source)
    fragments = _ToolFragments(source)
    fragments.replace(
        "sample", "  sample:\n    parameters: []\n    effects: []\n    metadata: *shared\n"
    )
    rendered = fragments.render()
    assert entry.text == source
    actual = _same(rendered, entry)
    assert actual["tools"]["sample"]["metadata"] is actual["definitions"]["actual_shared"]


def test_changed_non_tool_root_bytes_reject_the_seed(entry):
    source = _document()
    defense._load_runtime(source)
    fragments = _ToolFragments(source)
    fragments.replace("sample", "  sample:\n    parameters: []\n    effects: []\n")
    rendered = fragments.render()
    # Start a fresh exact source entry, then present the same attested splice plus a root edit.
    defense._load_runtime(source)
    changed_root = rendered.replace("version: 1", "version: 2")
    assert not generator.seed_generator_tool_fragments(fragments, changed_root)
    assert entry.text == source
    assert _same(changed_root, entry)["version"] == 2


@pytest.mark.parametrize("configuration", ["implicit", "path"])
def test_between_read_resolver_change_rejects_sparse_seed(entry, monkeypatch, configuration):
    source = _document()
    if configuration == "path":
        source = source.replace("numeric: 123", "numeric: '123'")
    defense._load_runtime(source)
    if configuration == "implicit":
        registry = {
            key: list(value) for key, value in yaml.CSafeLoader.yaml_implicit_resolvers.items()
        }
        registry["1"].insert(0, ("tag:yaml.org,2002:str", re.compile(r"^123$")))
        monkeypatch.setattr(yaml.CSafeLoader, "yaml_implicit_resolvers", registry)
    else:
        registry = dict(yaml.CSafeLoader.yaml_path_resolvers)
        monkeypatch.setattr(yaml.CSafeLoader, "yaml_path_resolvers", registry)
        yaml.CSafeLoader.add_path_resolver(
            "tag:yaml.org,2002:int", ["definitions", "numeric"], yaml.ScalarNode
        )
    rendered = senses._replace_tools(source, {"sample": _tool()})
    assert entry.text == source
    assert _same(rendered, entry)["definitions"]["numeric"] == (
        123 if configuration == "path" else "123"
    )


def test_absent_exact_cached_input_does_not_seed(entry):
    source = _document()
    rendered = senses._replace_tools(source, {"sample": _tool()})
    assert entry.text is None
    _same(rendered, entry)


@pytest.mark.parametrize(
    "fragment",
    [
        "  sample:\n    description: !unknown actual\n",
        "  sample:\n    description: [unfinished\n",
        "  sample: &shared\n    parameters: []\n    effects: []\n",
    ],
)
def test_unsafe_fragment_falls_back_to_ordinary_exact_error(entry, fragment):
    source = _document().replace("  numeric: 123", "  numeric: 123\n  existing: &shared {}")
    defense._load_runtime(source)
    fragments = _ToolFragments(source)
    fragments.replace("sample", fragment)
    rendered = fragments.render()
    assert entry.text == source
    with pytest.raises(Exception) as expected:
        yaml.load(rendered, Loader=yaml.CSafeLoader)
    with pytest.raises(type(expected.value)) as actual:
        defense._load_runtime(rendered)
    assert str(actual.value) == str(expected.value)
    assert entry.text is None and entry.node is None and entry.value is None


def test_subsequent_replacements_keep_new_order_and_fresh_unchanged_nodes(entry):
    source = _document()
    defense._load_runtime(source)
    first = senses._replace_tools(source, {"sample": _tool("First"), "new_first": _tool()})
    defense._load_runtime(first)
    second = senses._replace_tools(first, {"sample": _tool("Second"), "new_second": _tool()})
    actual = _same(second, entry)
    assert entry.text == second
    assert list(actual["tools"]) == ["sample", "untouched", "new_first", "new_second"]
    assert actual["tools"]["sample"]["description"] == "Second"


def test_unchanged_recursive_aliases_keep_identity_and_fresh_ownership(entry):
    source = _document().replace(
        "  numeric: 123",
        "  numeric: 123\n  cycle: &cycle {self: *cycle, notes: [one]}\n  alias: *cycle",
    )
    before = defense._load_runtime(source)
    rendered = senses._replace_tools(source, {"sample": _tool()})
    assert entry.text == rendered
    actual = _same(rendered, entry)
    cycle = actual["definitions"]["cycle"]
    assert cycle is cycle["self"] and cycle is actual["definitions"]["alias"]
    assert cycle is not before["definitions"]["cycle"]
    cycle["notes"].append("private mutation")
    assert defense._load_runtime(rendered)["definitions"]["cycle"]["notes"] == ["one"]


def test_custom_constructor_falls_back_and_runs_again_on_each_read(entry, monkeypatch):
    source = _document()
    defense._load_runtime(source)
    calls = []
    original = yaml.CSafeLoader.yaml_constructors["tag:yaml.org,2002:str"]

    def observed(loader, node):
        calls.append(node.value)
        return original(loader, node)

    constructors = dict(yaml.CSafeLoader.yaml_constructors)
    constructors["tag:yaml.org,2002:str"] = observed
    monkeypatch.setattr(yaml.CSafeLoader, "yaml_constructors", constructors)
    rendered = senses._replace_tools(source, {"sample": _tool()})
    assert entry.text == source
    first = defense._load_runtime(rendered)
    count = len(calls)
    second = defense._load_runtime(rendered)
    assert len(calls) > count
    _same_graph(first, second)
    assert first["tools"]["untouched"] is not second["tools"]["untouched"]


def test_existing_custom_path_resolution_is_not_rebased_into_a_fragment(entry, monkeypatch):
    monkeypatch.setattr(
        yaml.CSafeLoader, "yaml_path_resolvers", dict(yaml.CSafeLoader.yaml_path_resolvers)
    )
    yaml.CSafeLoader.add_path_resolver(
        "tag:yaml.org,2002:int", ["tools", "sample", "description"], yaml.ScalarNode
    )
    source = _document().replace("description: Old", "description: '123'")
    assert defense._load_runtime(source)["tools"]["sample"]["description"] == 123
    rendered = senses._replace_tools(source, {"sample": _tool("456")})
    assert entry.text == source
    actual = _same(rendered, entry)
    assert actual["tools"]["sample"]["description"] == 456


@pytest.mark.parametrize("custom", [False, True], ids=["safe-loader", "custom-subclass"])
def test_other_safe_loaders_keep_their_ordinary_changed_document_lifecycle(
    entry, monkeypatch, custom
):
    calls = []
    if custom:
        class CustomLoader(yaml.CSafeLoader):
            pass

        loader_type = CustomLoader
        monkeypatch.setattr(yaml, "CSafeLoader", loader_type)
    else:
        monkeypatch.delattr(yaml, "CSafeLoader")
        loader_type = yaml.SafeLoader
    original = loader_type.get_single_node

    def observed(self):
        calls.append(self)
        return original(self)

    monkeypatch.setattr(loader_type, "get_single_node", observed)
    source = _document()
    defense._load_runtime(source)
    rendered = senses._replace_tools(source, {"sample": _tool()})
    assert entry.text == source
    assert len(calls) == 1
    actual = defense._load_runtime(rendered)
    assert len(calls) == 2
    assert actual["tools"]["sample"]["description"] == "Actual changed fragment"
    _same(rendered, entry)
