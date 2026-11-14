"""Prove scoped composition reuse keeps safe loading and fresh emitter ownership."""

import math
from io import StringIO

import pytest
import yaml
from scripts import build_srd_5_2_1_spells as builder
from scripts import srd_timed_actor_defenses as defense
from scripts.srd_full.srd_runtime_defaults import restore_tool_defaults

from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations

RUNTIME = """shared: &shared {notes: [one, two], ready: true}
copy: *shared
base: &base {plain: original, level: 3}
merged: {<<: *base, level: 4}
tools:
  sample:
    description: Actual emitter input
    parameters: [{name: actor_state, resolve: entity}]
    effects: []
"""


def _count_compositions(monkeypatch, loader=None):
    loader = loader or getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    calls = []
    original = loader.get_single_node

    def observed(self):
        calls.append(self)
        return original(self)

    monkeypatch.setattr(loader, "get_single_node", observed)
    return calls


def _ordinary(text):
    data = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    return restore_tool_defaults(resolve_parameter_declarations(data))


def _same_graph(left, right):
    """Compare types, key order and both directions of recursive container aliasing."""
    forward, reverse = {}, {}

    def walk(a, b):
        assert type(a) is type(b)
        if isinstance(a, (dict, list, tuple, set)):
            aid, bid = id(a), id(b)
            if aid in forward:
                assert forward[aid] == bid
                return
            assert bid not in reverse
            forward[aid], reverse[bid] = bid, aid
            assert len(a) == len(b)
            if isinstance(a, dict):
                for (ak, av), (bk, bv) in zip(a.items(), b.items(), strict=True):
                    walk(ak, bk)
                    walk(av, bv)
            elif isinstance(a, set):
                assert a == b
            else:
                for av, bv in zip(a, b, strict=True):
                    walk(av, bv)
        elif isinstance(a, float) and math.isnan(a):
            assert math.isnan(b)
        else:
            assert a == b

    walk(left, right)


def test_main_composes_once_for_repeated_real_emitter_reads(tmp_path, monkeypatch):
    calls = _count_compositions(monkeypatch)
    names = {
        "CORPUS_PATH",
        "PLAYING_THE_GAME_CORPUS_PATH",
        "RULES_GLOSSARY_CORPUS_PATH",
        "SPELL_MECHANICS_PATH",
        "RUNTIME_PATH",
        "RECORD_PATH",
        "DECLARATIONS_PATH",
        "IDENTITY_PATH",
        "INVENTORY_PATH",
        "SPELL_MECHANICS_SOURCE_PATH",
        "NON_BEAST_FORMS_PATH",
        "MAGIC_MISSILE_SOURCE_PATH",
        "SUPPORT_PATH",
        "REFERENCE_RECORD_PATH",
        "REFERENCE_DECLARATIONS_PATH",
    }
    for name in names:
        path = tmp_path / name
        path.write_text(RUNTIME if name == "RUNTIME_PATH" else "{}\n", encoding="utf-8")
        monkeypatch.setattr(builder, name, path)
    writes = []
    monkeypatch.setattr(builder, "_replace_bytes", lambda path, value: writes.append((path, value)))

    def compile_fixture(**inputs):
        source = inputs["runtime_source"]
        first = defense._load_runtime(source)
        first["shared"]["notes"].append("local emitter mutation")
        for _ in range(2):
            current = defense._load_runtime(source)
            assert current["shared"]["notes"] == ["one", "two"]
            assert current["shared"] is current["copy"]
        names = {
            "runtime",
            "inventory",
            "source_map",
            "non_beast_forms",
            "magic_missile_source",
            "record",
            "declarations",
            "support",
            "reference_record",
            "reference_declarations",
        }
        return {name: source.encode() if name == "runtime" else b"{}\n" for name in names}, {
            "total": 0,
            "implemented": 0,
            "pending": 0,
        }

    monkeypatch.setattr(builder, "_build_spell_outputs", compile_fixture)
    builder.main()
    assert len(calls) == 1
    assert len(writes) == 10 and writes[0][1] == RUNTIME.encode()
    defense._load_runtime(RUNTIME)
    assert len(calls) == 2, "main must release its reuse scope"


def test_outside_scope_and_nonstring_inputs_keep_ordinary_loading(monkeypatch):
    from scripts.srd_full.srd_generator_yaml import scoped_generator_yaml

    calls = _count_compositions(monkeypatch)
    defense._load_runtime(RUNTIME)
    defense._load_runtime(RUNTIME)
    assert len(calls) == 2

    @scoped_generator_yaml
    def run():
        for value in (RUNTIME.encode(), RUNTIME.encode(), StringIO(RUNTIME), StringIO(RUNTIME)):
            _same_graph(_ordinary(RUNTIME), defense._load_runtime(value))

    before = len(calls)
    run()
    assert len(calls) - before == 8, "non-string inputs and ordinary comparison must all parse"


def test_exact_string_entry_invalidates_and_nested_scopes_release(monkeypatch):
    from scripts.srd_full.srd_generator_yaml import scoped_generator_yaml

    calls = _count_compositions(monkeypatch)
    changed = RUNTIME.replace("level: 4", "level: 5")

    @scoped_generator_yaml
    def inner():
        defense._load_runtime(RUNTIME)
        defense._load_runtime(RUNTIME)

    @scoped_generator_yaml
    def outer():
        defense._load_runtime(RUNTIME)
        inner()
        defense._load_runtime(RUNTIME)
        assert len(calls) == 2
        assert defense._load_runtime(changed)["merged"]["level"] == 5
        defense._load_runtime(RUNTIME)
        assert len(calls) == 4

    outer()
    defense._load_runtime(RUNTIME)
    assert len(calls) == 5


VALID = (
    RUNTIME,
    "a: &a {self: *a, rows: [*a]}\n",
    "a: &a [*a]\n",
    "base: &b {a: 1, b: 2}\nother: &o {a: 7, c: 3}\nresult: {<<: [*b, *o], b: 9, z: 1, z: 2}\n",
    "a: &s long text\nb: *s\nstamp: &t 2024-09-01\nstamp_copy: *t\n",
    "rows: [!!int '0x2a', !!float '.nan', !!bool 'yes', !!null '', "
    "!!timestamp '2024-09-01T01:02:03Z', !!binary 'aGVsbG8=']\n"
    "set: !!set {a: null, b: null}\nomap: !!omap [{a: 1}, {b: 2}]\n"
    "pairs: !!pairs [{a: 1}, {a: 2}]\n",
    "a: |\n  line one\n  line two\nb: >-\n  αβ\n  🐉\nc: 'yes'\nd: '001'\n",
    "a: !!str {=: text}\n",
    "{true: 1, 1: 2, null: 3, 2.5: 4}\n",
    "parameter_declarations:\n  param_count: {name: count, type: integer, required: true}\n"
    "tools:\n  sample:\n    description: Sample\n"
    "    parameters: [param_count, param_count]\n    effects: []\n",
)
INVALID = (
    "a: !unknown value\n",
    "a: !!python/object/apply:os.system ['opaque']\n",
    "a: !!map scalar\n",
    "a: !!seq scalar\n",
    "a: !!str [one]\n",
    "a: {<<: 1}\n",
    "? [one, two]\n: value\n",
    "a: *missing\n",
    "a: [one, two\n",
    "a: 1\n---\nb: 2\n",
    "a: !!int broken\n",
    "",
    "---\n",
    "a: &same {}\nb: &same {}\n",
    "!!str null\n",
    "parameter_declarations: {}\ntools:\n  sample:\n    parameters: [missing]\n    effects: []\n",
)


def test_safe_values_aliases_merges_declarations_and_freshness_match():
    from scripts.srd_full.srd_generator_yaml import scoped_generator_yaml

    @scoped_generator_yaml
    def run(source, expected):
        for _ in range(3):
            actual = defense._load_runtime(source)
            _same_graph(expected, actual)
            actual["_local_mutation"] = True
        assert "_local_mutation" not in defense._load_runtime(source)

    for source in VALID:
        run(source, _ordinary(source))


def test_safe_errors_and_failed_construction_clear_the_entry(monkeypatch):
    from scripts.srd_full.srd_generator_yaml import scoped_generator_yaml

    for source in INVALID:
        with pytest.raises(Exception) as expected:
            _ordinary(source)

        @scoped_generator_yaml
        def fail(source=source, expected_error=expected.value):
            for _ in range(2):
                with pytest.raises(type(expected_error)) as actual:
                    defense._load_runtime(source)
                assert str(actual.value) == str(expected_error)

        fail()
    calls = _count_compositions(monkeypatch)

    @scoped_generator_yaml
    def recovered():
        defense._load_runtime(RUNTIME)
        for invalid in ("a: !unknown value\n", INVALID[-1]):
            with pytest.raises((yaml.YAMLError, ValueError)):
                defense._load_runtime(invalid)
            defense._load_runtime(RUNTIME)

    recovered()
    assert len(calls) == 5, "safe construction and declaration errors must both clear reuse"


def test_generator_exception_releases_all_retained_nodes(monkeypatch):
    from scripts import srd_generator_yaml as generator_yaml

    calls = _count_compositions(monkeypatch)
    retained = []

    @generator_yaml.scoped_generator_yaml
    def fail():
        defense._load_runtime(RUNTIME)
        retained.append(generator_yaml._active_reuse.get())
        raise RuntimeError("generator failed after loading")

    with pytest.raises(RuntimeError, match="generator failed after loading"):
        fail()
    assert generator_yaml._active_reuse.get() is None
    assert retained[0].text is None and retained[0].node is None
    assert retained[0].value is None and not retained[0].has_value
    defense._load_runtime(RUNTIME)
    assert len(calls) == 2


def test_safe_loader_fallback_and_scalar_alias_identity(monkeypatch):
    from scripts.srd_full.srd_generator_yaml import scoped_generator_yaml

    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    calls = _count_compositions(monkeypatch, yaml.SafeLoader)
    source = "a: &s text\nb: *s\nstamp: &t 2024-09-01\nstamp_copy: *t\n"

    @scoped_generator_yaml
    def run():
        for _ in range(2):
            value = defense._load_runtime(source)
            assert value["a"] is value["b"] and value["stamp"] is value["stamp_copy"]

    run()
    assert len(calls) == 1


def test_repeated_builtin_reads_construct_once_without_sharing_mutable_values(monkeypatch):
    from scripts.srd_full.srd_generator_yaml import scoped_generator_yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    constructions = []
    original = loader.construct_document

    def observed(self, node):
        constructions.append(node)
        return original(self, node)

    monkeypatch.setattr(loader, "construct_document", observed)

    @scoped_generator_yaml
    def run():
        first = defense._load_runtime(RUNTIME)
        first["shared"]["notes"].append("first caller mutation")
        first["tools"]["sample"]["parameters"][0]["name"] = "changed"
        second = defense._load_runtime(RUNTIME)
        assert second["shared"]["notes"] == ["one", "two"]
        assert second["shared"] is second["copy"]
        assert second["tools"]["sample"]["parameters"][0]["name"] == "actor_state"
        third = defense._load_runtime(RUNTIME)
        assert third == second and third is not second

    run()
    assert len(constructions) == 1


def test_custom_safe_constructors_continue_to_run_fresh_on_every_read(monkeypatch):
    from scripts.srd_full.srd_generator_yaml import scoped_generator_yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    constructors = dict(loader.yaml_constructors)
    counts = []

    def counter(instance, node):
        instance.construct_scalar(node)
        counts.append(node)
        return len(counts)

    constructors["!counter"] = counter
    monkeypatch.setattr(loader, "yaml_constructors", constructors)

    @scoped_generator_yaml
    def run():
        for expected in (1, 2, 3):
            assert defense._load_runtime("a: !counter actual\n")["a"] == expected

    run()


def test_constructor_changes_invalidate_private_value_without_reparsing(monkeypatch):
    from scripts.srd_full.srd_generator_yaml import load_generator_yaml, scoped_generator_yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    original = loader.yaml_constructors
    calls = _count_compositions(monkeypatch)
    counts = []

    def custom_string(instance, node):
        counts.append(node)
        return instance.construct_scalar(node) + str(len(counts))

    @scoped_generator_yaml
    def run():
        assert load_generator_yaml("actual") == "actual"
        constructors = dict(original)
        constructors["tag:yaml.org,2002:str"] = custom_string
        monkeypatch.setattr(loader, "yaml_constructors", constructors)
        assert load_generator_yaml("actual") == "actual1"
        assert load_generator_yaml("actual") == "actual2"
        monkeypatch.setattr(loader, "yaml_constructors", original)
        assert load_generator_yaml("actual") == "actual"
        assert load_generator_yaml("actual") == "actual"

    run()
    assert len(calls) == 1
