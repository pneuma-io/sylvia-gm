"""Scoped schema misses preserve byte and safe-loader contracts."""

import re

import pytest
import yaml
from scripts import build_srd_5_2_1_spells as builder
from scripts import srd_generator_yaml as generator
from scripts import srd_timed_actor_defenses as defense

from tests.srd_full.test_srd_generator_yaml import _same_graph

SCHEMA = {
    "type": "object",
    "properties": {"family": {"const": "actual"}},
    "required": ["family"],
    "additionalProperties": False,
}


def _source():
    return (
        "version: 1\ndefinitions:\n  numeric: 123\ntools:\n"
        "  actual:\n    description: Unchanged owner\n    parameters: []\n    effects: []\n"
        "procedures: {}\ndefinition_row_schemas:\n  definitions.spells:\n"
        "    type: discriminated_union\n    discriminator: family\n    variants:\n"
        "      actual: {type: object}\n"
        "  definitions.other: {type: object}\n"
    )


@pytest.fixture
def entry():
    entry = generator._ComposedNodeReuse()
    token = generator._active_reuse.set(entry)
    try:
        yield entry
    finally:
        entry.clear()
        generator._active_reuse.reset(token)


@pytest.mark.parametrize("family", ["actual", "new_family"])
def test_actual_registrar_next_read_does_not_parse_whole_changed_document(
    entry, monkeypatch, family
):
    source = _source()
    streams = []
    original = yaml.CSafeLoader.__init__

    def observe(self, stream):
        streams.append(stream)
        original(self, stream)

    monkeypatch.setattr(yaml.CSafeLoader, "__init__", observe)
    original_graph = defense._load_runtime(source)
    rendered = builder._register_spell_family_schema(source, family, SCHEMA)
    actual = defense._load_runtime(rendered)
    assert rendered not in streams, "the actual schema registrar caused another whole parse"
    assert source in streams and entry.text == rendered
    assert (
        rendered.partition("definition_row_schemas:")[0]
        == source.partition("definition_row_schemas:")[0]
    )
    assert actual["definition_row_schemas"]["definitions.spells"]["variants"][family] == SCHEMA
    assert (
        actual["tools"] == original_graph["tools"]
        and actual["tools"] is not original_graph["tools"]
    )
    token = generator._active_reuse.set(None)
    try:
        ordinary = defense._load_runtime(rendered)
    finally:
        generator._active_reuse.reset(token)
    _same_graph(actual, ordinary)
    actual["tools"]["actual"]["description"] = "caller-local"
    assert defense._load_runtime(rendered)["tools"]["actual"]["description"] == "Unchanged owner"


def _parity(rendered):
    token = generator._active_reuse.set(None)
    try:
        expected = defense._load_runtime(rendered)
    finally:
        generator._active_reuse.reset(token)
    actual = defense._load_runtime(rendered)
    _same_graph(expected, actual)
    return actual


def test_inside_schema_aliases_and_recursive_values_remain_shared_and_fresh(entry):
    source = _source().replace(
        "  definitions.other: {type: object}",
        "  definitions.other: &inside {type: object, recursive: *inside}\n"
        "  definitions.linked: *inside",
    )
    defense._load_runtime(source)
    rendered = builder._register_spell_family_schema(source, "actual", SCHEMA)
    assert entry.text == source
    actual = _parity(rendered)
    assert entry.text == rendered
    schemas = actual["definition_row_schemas"]
    assert schemas["definitions.other"] is schemas["definitions.linked"]
    assert schemas["definitions.other"]["recursive"] is schemas["definitions.other"]
    schemas["definitions.other"]["type"] = "local"
    fresh = defense._load_runtime(rendered)["definition_row_schemas"]
    assert fresh["definitions.other"]["type"] == "object"
    assert fresh["definitions.other"] is fresh["definitions.linked"]


def test_incoming_schema_alias_is_rejected_and_ordinary_identity_is_preserved(entry):
    prefix, schema = _source().split("definition_row_schemas:")
    source = (
        "definition_row_schemas:"
        + schema.replace(
            "  definitions.other: {type: object}", "  definitions.other: &shared {type: object}"
        )
        + prefix.replace("  numeric: 123", "  numeric: 123\n  borrowed: *shared")
    )
    defense._load_runtime(source)
    rendered = builder._register_spell_family_schema(source, "actual", SCHEMA)
    assert entry.text == source
    actual = _parity(rendered)
    assert (
        actual["definitions"]["borrowed"] is actual["definition_row_schemas"]["definitions.other"]
    )


def test_external_schema_alias_uses_the_original_document_loader(entry):
    source = (
        _source()
        .replace("  numeric: 123", "  numeric: 123\n  shared: &outside {type: object}")
        .replace("  definitions.other: {type: object}", "  definitions.other: *outside")
    )
    defense._load_runtime(source)
    rendered = builder._register_spell_family_schema(source, "actual", SCHEMA)
    assert entry.text == source
    actual = _parity(rendered)
    assert actual["definitions"]["shared"] is actual["definition_row_schemas"]["definitions.other"]


def test_merge_source_uses_original_loader_after_flattening(entry):
    source = _source().replace(
        "  definitions.other: {type: object}",
        "  definitions.other: &base {type: object}\n  definitions.merged: {<<: *base, actual: 123}",
    )
    defense._load_runtime(source)
    rendered = builder._register_spell_family_schema(source, "actual", SCHEMA)
    assert entry.text == source
    assert _parity(rendered)["definition_row_schemas"]["definitions.merged"]["actual"] == 123


@pytest.mark.parametrize("edit", ["tool", "other_root", "root_order", "new_anchor"])
def test_only_attested_schema_section_changes_can_seed(entry, edit):
    source = _source()
    defense._load_runtime(source)
    token = generator._active_reuse.set(None)
    try:
        rendered = builder._register_spell_family_schema(source, "actual", SCHEMA)
    finally:
        generator._active_reuse.reset(token)
    if edit == "tool":
        rendered = rendered.replace("Unchanged owner", "Changed owner")
    elif edit == "other_root":
        rendered = rendered.replace("numeric: 123", "numeric: 456")
    elif edit == "root_order":
        rendered = rendered.replace("version: 1\n", "") + "version: 1\n"
    else:
        rendered = rendered.replace("definitions.other: {", "definitions.other: &new {")
    assert generator.seed_generator_definition_schemas(source, rendered) is False
    assert entry.text == source
    _parity(rendered)


@pytest.mark.parametrize(
    "section",
    [
        "  definitions.other: !!binary SGVsbG8=\n  date: 2026-10-02\n  set: !!set {one: null}\n"
        '  pairs: !!pairs [{one: 1}]\n  omap: !!omap [{one: 1}]\n  quoted: "123"',
        "  definitions.other: {duplicate: old, duplicate: new}",
    ],
)
def test_builtin_special_types_and_duplicate_keys_have_original_semantics(entry, section):
    source = _source().replace("  definitions.other: {type: object}", section)
    defense._load_runtime(source)
    rendered = builder._register_spell_family_schema(source, "actual", SCHEMA)
    assert entry.text == source
    _parity(rendered)
    assert entry.text == rendered


def test_custom_safe_constructor_is_not_cached_or_seeded(entry, monkeypatch):
    original = yaml.CSafeLoader.yaml_constructors
    calls = []
    custom = dict(original)

    def custom_string(loader, node):
        calls.append(node.value)
        return original["tag:yaml.org,2002:str"](loader, node)

    custom["tag:yaml.org,2002:str"] = custom_string
    monkeypatch.setattr(yaml.CSafeLoader, "yaml_constructors", custom)
    source = _source()
    defense._load_runtime(source)
    rendered = builder._register_spell_family_schema(source, "actual", SCHEMA)
    assert entry.text == source
    _parity(rendered)
    calls.clear()
    defense._load_runtime(rendered)
    assert calls and not entry.has_value


@pytest.mark.parametrize("registry", ["implicit", "path"])
def test_changed_resolver_registry_rejects_seed_and_refreshes_values(entry, monkeypatch, registry):
    source = _source()
    if registry == "path":
        source = source.replace("numeric: 123", 'numeric: "123"')
    defense._load_runtime(source)
    if registry == "implicit":
        values = {
            key: list(value) for key, value in yaml.CSafeLoader.yaml_implicit_resolvers.items()
        }
        values["1"].insert(0, ("tag:yaml.org,2002:str", re.compile("^123$")))
        monkeypatch.setattr(yaml.CSafeLoader, "yaml_implicit_resolvers", values)
    else:
        values = dict(yaml.CSafeLoader.yaml_path_resolvers)
        monkeypatch.setattr(yaml.CSafeLoader, "yaml_path_resolvers", values)
        yaml.CSafeLoader.add_path_resolver("tag:yaml.org,2002:int", ["definitions", "numeric"])
    rendered = builder._register_spell_family_schema(source, "actual", SCHEMA)
    assert entry.text == source
    assert _parity(rendered)["definitions"]["numeric"] == ("123" if registry == "implicit" else 123)


@pytest.mark.parametrize("invalid", ["unknown_tag", "malformed_binary", "removed_anchor"])
def test_invalid_fragment_falls_back_then_original_read_clears_entry(entry, invalid):
    source = _source()
    if invalid == "removed_anchor":
        source = source.replace(
            "      actual: {type: object}",
            "      actual: &original {type: object}\n      linked: *original",
        )
    defense._load_runtime(source)
    token = generator._active_reuse.set(None)
    try:
        rendered = builder._register_spell_family_schema(source, "actual", SCHEMA)
    finally:
        generator._active_reuse.reset(token)
    if invalid == "unknown_tag":
        rendered = rendered.replace(
            "definitions.other: {type: object}", "definitions.other: !unknown value"
        )
    elif invalid == "malformed_binary":
        rendered = rendered.replace(
            "definitions.other: {type: object}", "definitions.other: !!binary not-base64"
        )
    assert not generator.seed_generator_definition_schemas(source, rendered)
    assert entry.text == source
    with pytest.raises(yaml.YAMLError):
        defense._load_runtime(rendered)
    assert entry.text is None and entry.node is None and not entry.has_value


def test_scope_exit_inactive_and_unchanged_inputs_do_not_seed(entry):
    source = _source()
    defense._load_runtime(source)
    assert not generator.seed_generator_definition_schemas(source, source)
    token = generator._active_reuse.set(None)
    try:
        rendered = builder._register_spell_family_schema(source, "actual", SCHEMA)
        assert not generator.seed_generator_definition_schemas(source, rendered)
    finally:
        generator._active_reuse.reset(token)
    assert entry.text == source
    captured = []

    @generator.scoped_generator_yaml
    def scoped():
        captured.append(generator._active_reuse.get())
        defense._load_runtime(source)
        return builder._register_spell_family_schema(source, "actual", SCHEMA)

    assert scoped() == rendered and captured[0].text is None


@pytest.mark.parametrize("loader", ["subclass", "python"])
def test_custom_loader_fallback_retains_ordinary_parser(entry, monkeypatch, loader):
    if loader == "subclass":

        class Custom(yaml.CSafeLoader):
            pass

        monkeypatch.setattr(yaml, "CSafeLoader", Custom)
    else:
        monkeypatch.delattr(yaml, "CSafeLoader")
    source = _source()
    defense._load_runtime(source)
    rendered = builder._register_spell_family_schema(source, "actual", SCHEMA)
    assert entry.text == source
    _parity(rendered)


def test_lazy_reader_reuses_schema_only_edits_without_a_producer_hook(entry, monkeypatch):
    source = _source()
    defense._load_runtime(source)
    rendered = source.replace(
        "definitions.other: {type: object}", "definitions.other: {type: string}"
    )
    streams = []
    original = yaml.CSafeLoader.__init__

    def observe(self, stream):
        streams.append(stream)
        original(self, stream)

    monkeypatch.setattr(yaml.CSafeLoader, "__init__", observe)
    actual = generator.load_generator_yaml(rendered)
    assert rendered not in streams
    assert entry.text == rendered
    assert actual["definition_row_schemas"]["definitions.other"]["type"] == "string"
    _same_graph(actual, yaml.load(rendered, Loader=yaml.CSafeLoader))


def test_lazy_reader_mixed_tool_and_schema_edit_reparses_original_document(entry, monkeypatch):
    source = _source()
    generator.load_generator_yaml(source)
    rendered = source.replace(
        "definitions.other: {type: object}", "definitions.other: {type: string}"
    )
    rendered = rendered.replace("Unchanged owner", "Changed owner")
    streams = []
    original = yaml.CSafeLoader.__init__

    def observe(self, stream):
        streams.append(stream)
        original(self, stream)

    monkeypatch.setattr(yaml.CSafeLoader, "__init__", observe)
    actual = generator.load_generator_yaml(rendered)
    assert rendered in streams and entry.text == rendered
    assert actual["tools"]["actual"]["description"] == "Changed owner"
    _same_graph(actual, yaml.load(rendered, Loader=yaml.CSafeLoader))


def test_lazy_seed_interruption_releases_the_original_cached_graph(entry, monkeypatch):
    source = _source()
    generator.load_generator_yaml(source)
    rendered = source.replace(
        "definitions.other: {type: object}", "definitions.other: {type: string}"
    )

    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(generator, "seed_generator_definition_schemas", interrupted)
    with pytest.raises(KeyboardInterrupt):
        generator.load_generator_yaml(rendered)
    assert entry.text is None and entry.node is None and not entry.has_value
