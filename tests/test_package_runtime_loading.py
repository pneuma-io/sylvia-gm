"""Keep first-use package preparation bounded without dropping runtime validation."""

from copy import deepcopy

import pytest
import yaml

from sylvia_gm.application import package_runtime
from sylvia_gm.application.package_runtime import PackageRuntimeError, _parse_runtime
from sylvia_gm.domain.authority import MechanicContext, owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset, validate_ruleset_data
from tests.test_alpha_f3_package_execution import _runtime_yaml


@pytest.mark.parametrize('authoring', [False, True])
def test_runtime_checks_source_and_expanded_parameter_shape_without_a_duplicate_scan(
    monkeypatch, authoring,
):
    data = yaml.safe_load(_runtime_yaml())
    data['parameter_declarations'] = {'steps': data['tools']['advance']['parameters'][0]}
    data['tools']['advance']['parameters'][0] = 'steps'
    checked = []
    validate = package_runtime._validate_shape

    def observed(value):
        checked.append(deepcopy(value))
        validate(value)

    monkeypatch.setattr(package_runtime, '_validate_shape', observed)
    package_runtime._clear_runtime_cache()
    runtime = _parse_runtime(
        package_id='synthetic.clockwork',
        runtime_bytes=yaml.safe_dump(data, sort_keys=False).encode(), authoring=authoring,
    )
    assert runtime.tools['advance'].parameters[0].name == 'steps'
    assert len(checked) == 2
    assert checked[0]['tools']['advance']['parameters'][0] == 'steps'
    assert checked[1]['tools']['advance']['parameters'][0]['name'] == 'steps'


@pytest.mark.parametrize('authoring', [False, True])
def test_expanded_parameter_references_still_spend_the_full_shape_budget(monkeypatch, authoring):
    data = yaml.safe_load(_runtime_yaml())
    data['parameter_declarations'] = {'steps': data['tools']['advance']['parameters'][0]}
    data['tools']['advance']['parameters'][0] = 'steps'
    for index in range(12):
        data['tools'][f'sibling_{index}'] = deepcopy(data['tools']['advance'])

    def nodes(value):
        children = (
            value.values() if isinstance(value, dict)
            else value if isinstance(value, list) else ()
        )
        return 1 + sum(nodes(child) for child in children)

    monkeypatch.setattr(package_runtime, '_MAX_RUNTIME_NODES', nodes(data) + 3)
    package_runtime._clear_runtime_cache()
    with pytest.raises(PackageRuntimeError, match='too many nodes'):
        _parse_runtime(
            package_id='synthetic.clockwork',
            runtime_bytes=yaml.safe_dump(data, sort_keys=False).encode(), authoring=authoring,
        )
    assert not package_runtime._PARSED_RUNTIMES


@pytest.mark.parametrize('authoring', [False, True])
def test_runtime_validates_one_parsed_document_and_reuses_exact_bytes(monkeypatch, authoring):
    """Executable validation must not parse the complete ruleset a second time."""
    source = _runtime_yaml()
    original_load = yaml.load
    parses = []

    def counted_load(stream, **kwargs):
        parses.append(stream)
        return original_load(stream, **kwargs)

    package_runtime._clear_runtime_cache()
    monkeypatch.setattr(yaml, 'load', counted_load)
    first = _parse_runtime(
        package_id='synthetic.clockwork', runtime_bytes=source, authoring=authoring,
    )
    second = _parse_runtime(
        package_id='synthetic.clockwork', runtime_bytes=source, authoring=authoring,
    )
    assert 'advance' in first.tools
    assert second is first
    assert len(parses) == 1


def test_execution_reuses_stricter_parse_but_authored_content_is_always_checked(monkeypatch):
    source = _runtime_yaml()
    original_load = yaml.load
    parses = []

    def counted_load(stream, **kwargs):
        parses.append(stream)
        return original_load(stream, **kwargs)

    package_runtime._clear_runtime_cache()
    monkeypatch.setattr(yaml, 'load', counted_load)
    authored = _parse_runtime(
        package_id='synthetic.clockwork', runtime_bytes=source, authoring=True,
    )
    executed = _parse_runtime(package_id='synthetic.clockwork', runtime_bytes=source)
    assert executed is authored
    assert len(parses) == 1
    # A permissive legacy execution parse must never attest strict authoring validation.
    duplicate = source + b'tools: {}\n'
    _parse_runtime(package_id='synthetic.clockwork', runtime_bytes=duplicate)
    with pytest.raises(PackageRuntimeError, match='YAML is invalid'):
        _parse_runtime(package_id='synthetic.clockwork', runtime_bytes=duplicate, authoring=True)
    # Even a one-byte change needs fresh admission; package identity is also part of the key.
    _parse_runtime(package_id='synthetic.clockwork', runtime_bytes=source + b'\n')
    with pytest.raises(PackageRuntimeError, match='does not match'):
        _parse_runtime(package_id='another.ruleset', runtime_bytes=source)
    assert len(parses) == 5


@pytest.mark.parametrize('invalid', [False, True])
def test_parsed_validation_keeps_executable_checks_and_does_not_mutate_data(invalid):
    data = yaml.safe_load(_runtime_yaml())
    if invalid:
        data['tools']['advance']['effects'] = [{'unknown_operation': {}}]
    original = deepcopy(data)
    errors = validate_ruleset_data(data)
    assert errors == validate_ruleset(yaml.safe_dump(data))
    assert bool(errors) is invalid
    assert data == original


@pytest.mark.parametrize('authoring', [False, True])
def test_runtime_rejects_python_object_tags(authoring):
    source = _runtime_yaml().replace(
        b'definitions: {}', b'definitions: !!python/object:builtins.dict {}',
    )
    with pytest.raises(PackageRuntimeError, match='YAML is invalid'):
        _parse_runtime(package_id='synthetic.clockwork', runtime_bytes=source, authoring=authoring)


def test_authored_runtime_preserves_yaml_merges_and_rejects_duplicate_keys():
    source = _runtime_yaml().replace(
        b'definitions: {}',
        b'definitions: {base: &base {label: base}, copy: {<<: *base, label: copy}}',
    )
    runtime = _parse_runtime(package_id='synthetic.clockwork', runtime_bytes=source, authoring=True)
    assert runtime.definitions['copy']['label'] == 'copy'
    with pytest.raises(PackageRuntimeError, match='YAML is invalid'):
        _parse_runtime(
            package_id='synthetic.clockwork', runtime_bytes=source + b'tools: {}\n', authoring=True,
        )


@pytest.mark.parametrize('authoring', [False, True])
def test_runtime_bounds_yaml_merge_expansion_before_mapping_construction(monkeypatch, authoring):
    """Repeated merge aliases must spend the parser budget even when keys later collapse."""
    definitions = ['definitions:', '  base: &a0 {label: small}']
    definitions.extend(
        f'  merged{index}: &a{index} {{<<: [*a{index - 1}, *a{index - 1}]}}'
        for index in range(1, 12)
    )
    source = _runtime_yaml().replace(b'definitions: {}', '\n'.join(definitions).encode())
    monkeypatch.setattr(package_runtime, '_MAX_RUNTIME_NODES', 1024)
    package_runtime._clear_runtime_cache()

    def forbidden_expansion(*args, **kwargs):
        pytest.fail('YAML merges expanded before the parser budget was checked')

    monkeypatch.setattr(yaml.constructor.SafeConstructor, 'flatten_mapping', forbidden_expansion)
    with pytest.raises(PackageRuntimeError, match='limit|too many'):
        _parse_runtime(package_id='synthetic.clockwork', runtime_bytes=source, authoring=authoring)


@pytest.mark.parametrize('authoring', [False, True])
def test_runtime_rejects_recursive_yaml_merge_as_invalid_content(authoring):
    source = _runtime_yaml().replace(b'definitions: {}', b'definitions: &loop {<<: *loop}')
    with pytest.raises(PackageRuntimeError, match='recursive|YAML is invalid'):
        _parse_runtime(package_id='synthetic.clockwork', runtime_bytes=source, authoring=authoring)


def test_tool_private_data_is_copied_on_first_execution_and_stays_isolated(monkeypatch):
    source = _runtime_yaml().replace(
        b'definitions: {}', b'definitions: {lazy_copy_probe: {items: [1, 2, 3]}}',
    )
    runtime = _parse_runtime(package_id='synthetic.clockwork', runtime_bytes=source)
    declaration = ToolDeclaration(
        name='advance', capability='clockwork.advance',
        input_schema=owned_payload(package_runtime._runtime_parameter_schema(runtime.tools['advance'])),
        state_predicates=(), turn_statuses=(), writers=(),
    )
    copied = []
    original_copy = package_runtime.deepcopy

    def capture_copy(value):
        if isinstance(value, dict) and 'lazy_copy_probe' in value:
            copied.append(value)
        return original_copy(value)

    monkeypatch.setattr(package_runtime, 'deepcopy', capture_copy)
    mechanics = [package_runtime.materialize_package_mechanic(
        package_id='synthetic.clockwork', package_version='1.0.0', package_sha256='a' * 64,
        declaration=declaration, runtime_bytes=source,
    ) for _ in range(2)]
    assert copied == []
    seen = []
    original_execute = package_runtime.execute_tool

    def capture_execute(*args, **kwargs):
        config, definitions = args[4:6]
        seen.append((config, definitions))
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(package_runtime, 'execute_tool', capture_execute)
    for index in (0, 0, 1):
        result = mechanics[index](MechanicContext(
            campaign_id='campaign', turn_id='turn', action_id=f'action-{len(seen)}',
            state=owned_payload({'synthetic.clockwork.state': {'counter': 0, 'bonus': 0}}),
            parameters=owned_payload({'steps': 1}),
        ))
        assert result.mutations
    assert len(copied) == 2
    assert seen[0][0] is seen[1][0] and seen[0][1] is seen[1][1]
    assert seen[0][0] is not seen[2][0] and seen[0][1] is not seen[2][1]
    assert seen[0][0] is not runtime.ruleset_config
    assert seen[0][1]['lazy_copy_probe'] is not runtime.definitions['lazy_copy_probe']
