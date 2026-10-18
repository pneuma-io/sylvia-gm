"""Compose saved environment sight with existing scalar, multi and Orb attacks.

Private query copies forward the actual saved-state identity. Public declarations,
persisted parameter keys, profiles and spending/damage effects retain their owners.
"""
from copy import deepcopy
from io import StringIO
import re
import tokenize

from .srd_control_behaviors import _lasting
from .srd_timed_actor_defenses import _call, _require, _set, _when

OBSCURING = frozenset({'fog_cloud', 'sleet_storm', 'stinking_cloud'})
OWNERS = ('cast_spell_attack_damage', 'resolve_spell_multi_attack_target',
          'resolve_srd_chromatic_orb_leap')
SIGHT = {'caster_can_see_target': '_retained_caster_sees',
         'target_can_see_caster': '_retained_target_sees'}
NEARBY = {'enemy_within_5_feet': '_retained_environment_enemy_nearby',
          'close_enemy_can_see_caster': '_retained_environment_enemy_sees',
          'close_enemy_incapacitated': '_retained_environment_enemy_incapacitated'}
INPUTS = SIGHT | NEARBY
SIGHT_PRECONDITION = 'requires_sight == false or caster_can_see_target == true'
ENVIRONMENT_SIGHT_PRECONDITION = (
    'lasting_state.has_spell_environments == true or (' + SIGHT_PRECONDITION + ')')
QUERY_ROOTS = ('resolve_srd_scene_relation_node', 'resolve_srd_produce_flame_nearby_enemies')
QUERY_BYPASS = {'resolve_srd_scene_relation': 'resolve_srd_scene_relation_node',
                'resolve_srd_sight': 'resolve_srd_sight_node'}
QUERY_PREFIX = 'retained_spell_query_'
_TEMPLATE = re.compile(r'\{([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*)\}')


def _rewrite_expression(text, replacements):
    """Replace bare NAME tokens, retaining quoted keys and dotted properties."""
    if not isinstance(text, str):
        return text
    if not any(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text)
               for name in replacements):
        return text
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    edits = []
    previous = None
    try:
        tokens = tokenize.generate_tokens(StringIO(text).readline)
        for token in tokens:
            if token.type == tokenize.NAME and token.string in replacements and previous != '.':
                start = offsets[token.start[0] - 1] + token.start[1]
                end = offsets[token.end[0] - 1] + token.end[1]
                edits.append((start, end, replacements[token.string]))
            if token.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                                  tokenize.DEDENT, tokenize.COMMENT):
                previous = token.string
    except (tokenize.TokenError, IndentationError) as error:
        raise ValueError('A spell attack expression cannot be tokenized safely') from error
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    return text


def _expression_value(value, replacements):
    if isinstance(value, list):
        return [_expression_value(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _expression_value(item, replacements) for key, item in value.items()}
    return _rewrite_expression(value, replacements)


def _template_value(value, replacements):
    """Call parameters interpolate references; ordinary literal text stays literal."""
    if isinstance(value, list):
        return [_template_value(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _template_value(item, replacements) for key, item in value.items()}
    if isinstance(value, str):
        return _TEMPLATE.sub(lambda match: '{' + replacements.get(match[1], match[1]) + '}', value)
    return value


def replace_consumers(effects, replacements):
    """Rewrite executable input expressions while preserving saved destinations."""
    rows = deepcopy(effects)
    for effect in rows:
        if 'condition' in effect:
            effect['condition'] = _rewrite_expression(effect['condition'], replacements)
        if 'set' in effect:
            effect['set']['value'] = _expression_value(effect['set']['value'], replacements)
        if 'require' in effect:
            effect['require']['condition'] = _rewrite_expression(effect['require']['condition'], replacements)
        if 'call' in effect and 'params' in effect:
            effect['params'] = _template_value(effect['params'], replacements)
        for kind in ('list_push', 'dict_set'):
            if kind in effect and 'value' in effect[kind]:
                effect[kind]['value'] = _expression_value(effect[kind]['value'], replacements)
        for branch in ('on_true', 'on_false'):
            if branch in effect:
                effect[branch] = replace_consumers(effect[branch], replacements)
        if 'for_each' in effect:
            effect['for_each']['do'] = replace_consumers(effect['for_each']['do'], replacements)
    return rows


def sight_preconditions(owner, *, installed=False):
    """Defer only the sight gate when actual environment projection will run."""
    conditions = deepcopy(owner.get('pre_conditions', []))
    expected = ENVIRONMENT_SIGHT_PRECONDITION if installed else SIGHT_PRECONDITION
    matching = [row for row in conditions if row.get('condition') == expected]
    if len(matching) != 1:
        raise ValueError('The actual scalar sight precondition changed')
    if not installed:
        matching[0]['condition'] = ENVIRONMENT_SIGHT_PRECONDITION
    return conditions


def walk_effects(effects):
    for effect in effects:
        yield effect
        for branch in ('on_true', 'on_false'):
            yield from walk_effects(effect.get(branch, []))
        if 'for_each' in effect:
            yield from walk_effects(effect['for_each']['do'])


def query_name(name):
    return QUERY_PREFIX + QUERY_BYPASS.get(name, name)


def state_aliases(owner):
    """Derive saved-state aliases from actual entity-default-state declarations."""
    return {parameter['name'] for parameter in owner.get('parameters', [])
            if parameter.get('resolve') == 'entity' and parameter.get('default') == 'state'
            and parameter['name'] != 'lasting_state'}


def state_rebindings(owner):
    aliases = state_aliases(owner)
    return {'state': 'lasting_state',
            **{alias: 'lasting_state' for alias in aliases},
            **{'_' + alias + '_id': '_lasting_state_id' for alias in aliases}}


def _state_templates(value, replacements=None):
    replacements = {'state': 'lasting_state'} if replacements is None else replacements
    if isinstance(value, list):
        return [_state_templates(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _state_templates(item, replacements) for key, item in value.items()}
    if isinstance(value, str):
        return _TEMPLATE.sub(
            lambda match: '{' + '.'.join([replacements.get(match[1].split('.')[0], match[1].split('.')[0]),
                                         *match[1].split('.')[1:]]) + '}', value)
    return value


def forward_query_params(parameters, callee):
    """Keep unrelated inputs; bind every declared saved-state alias to one ID."""
    forwarded = deepcopy(parameters)
    forwarded['lasting_state'] = '{_lasting_state_id}'
    for alias in sorted(state_aliases(callee)):
        forwarded[alias] = '{_lasting_state_id}'
    return forwarded


def query_nodes(runtime):
    """Copy only state-dependent query owners and their actual call ancestors.

    The two public sight/relation wrappers are replaced by their accepted internal
    nodes. All original branches remain, including guarded tests and refreshes.
    Physical spatial paths retain the bridge's established state.spatial_scene
    convention; custom-state physical admission is a separate required check.
    """
    tools = runtime['tools']
    graph = {}
    pending = list(QUERY_ROOTS)
    while pending:
        original = pending.pop()
        name = QUERY_BYPASS.get(original, original)
        if name in graph:
            continue
        if name not in tools:
            raise ValueError('An actual private query owner is unavailable: ' + name)
        targets = []
        for effect in walk_effects(tools[name].get('effects', [])):
            if 'call' in effect:
                target = effect['call']
                if not isinstance(target, str) or '{' in target:
                    raise ValueError('A query has an unresolved dynamic tool owner: ' + name)
                targets.append(QUERY_BYPASS.get(target, target))
        graph[name] = set(targets)
        pending.extend(targets)

    stateful = {name for name in graph if state_aliases(tools[name]) or any(
        parameter['name'] == 'lasting_state' for parameter in tools[name].get('parameters', []))}
    # State reads in accepted executable expressions also need an explicit binding.
    for name in graph:
        owner = tools[name]
        if replace_consumers(owner.get('effects', []), {'state': 'lasting_state'}) != owner.get('effects', []):
            stateful.add(name)
        if any(_rewrite_expression(row.get('condition', ''), {'state': 'lasting_state'})
               != row.get('condition', '') for row in owner.get('pre_conditions', [])):
            stateful.add(name)
        if any(isinstance(effect.get(kind, {}).get(destination), str)
               and effect[kind][destination].startswith('state.')
               for effect in walk_effects(owner.get('effects', []))
               for kind, destination in (('set', 'field'), ('list_push', 'list'), ('dict_set', 'dict'))):
            stateful.add(name)
    while True:
        ancestors = {name for name, targets in graph.items() if targets & stateful}
        if ancestors <= stateful:
            break
        stateful |= ancestors
    if not set(QUERY_ROOTS) <= stateful:
        raise ValueError('An actual query root lacks its saved-state dependency')

    copies = {}
    for name in sorted(stateful):
        owner = deepcopy(tools[name])
        rebindings = state_rebindings(owner)
        if not any(parameter['name'] == 'lasting_state' for parameter in owner.get('parameters', [])):
            owner.setdefault('parameters', []).append(_lasting())
        owner['effects'] = replace_consumers(owner.get('effects', []), rebindings)
        for row in owner.get('pre_conditions', []):
            row['condition'] = _rewrite_expression(row['condition'], rebindings)
        for effect in walk_effects(owner['effects']):
            for kind, destination in (('set', 'field'), ('list_push', 'list'), ('dict_set', 'dict')):
                if kind in effect and isinstance(effect[kind].get(destination), str):
                    path_root, separator, suffix = effect[kind][destination].partition('.')
                    if separator and path_root in rebindings:
                        effect[kind][destination] = rebindings[path_root] + separator + suffix
            if 'call' in effect:
                target = QUERY_BYPASS.get(effect['call'], effect['call'])
                effect['params'] = _state_templates(effect.get('params', {}), rebindings)
                if target in stateful:
                    effect['call'] = query_name(target)
                    effect['params'] = forward_query_params(effect['params'], tools[target])
        owner.setdefault('metadata', {}).update(category='internal', visible_to_player=False)
        copies[query_name(name)] = owner
    return copies


def prefix(name):
    """Retain supplied facts outside environments; otherwise query actual owners."""
    effects = [_set(local, parameter) for parameter, local in INPUTS.items()]
    project = [
        _call(query_name('resolve_srd_scene_relation_node'), {
            'actor_state': '{_caster_state_id}', 'target_state': '{_target_id}',
            'lasting_state': '{_lasting_state_id}'}, ('_scene_can_see',)),
        _set(SIGHT['caster_can_see_target'], '_scene_can_see'),
        _call(query_name('resolve_srd_scene_relation_node'), {
            'actor_state': '{_target_id}', 'target_state': '{_caster_state_id}',
            'lasting_state': '{_lasting_state_id}'}, ('_scene_can_see',)),
        _set(SIGHT['target_can_see_caster'], '_scene_can_see'),
        _call(query_name('resolve_srd_produce_flame_nearby_enemies'), {
            'caster_state': '{_caster_state_id}', 'lasting_state': '{_lasting_state_id}'},
            ('_produce_nearby_disadvantage',)),
        _set(NEARBY['enemy_within_5_feet'], '_produce_nearby_disadvantage'),
        # The existing query has already joined proximity, hostile sight and
        # capability. These private flags preserve that consumer conjunction.
        _set(NEARBY['close_enemy_can_see_caster'], True),
        _set(NEARBY['close_enemy_incapacitated'], False),
    ]
    if name != 'resolve_spell_multi_attack_target':
        project.append(_require(
            'requires_sight == false or _retained_caster_sees == true',
            'The selected spell requires an actually visible target.'))
    return effects + [_when('lasting_state.has_spell_environments == true', project)]


def bind_environment_spell_attack_sight(runtime, selected):
    """Compose all three settled consumers atomically, without selecting a spell."""
    if not OBSCURING.intersection(selected):
        return []
    changed = {}
    nodes = query_nodes(runtime)
    for name, owner in nodes.items():
        if name in runtime['tools'] and runtime['tools'][name] != owner:
            raise ValueError('A partial private query owner changed: ' + name)
    for name in OWNERS:
        if name not in runtime['tools']:
            raise ValueError('An actual spell attack consumer is unavailable: ' + name)
        owner = deepcopy(runtime['tools'][name])
        parameters = {parameter['name'] for parameter in owner['parameters']}
        if not {'caster_state', 'target', *INPUTS} <= parameters:
            raise ValueError('A spell attack lacks its actual input declarations: ' + name)
        if name != 'resolve_spell_multi_attack_target' and 'requires_sight' not in parameters:
            raise ValueError('A scalar spell attack lacks its sight requirement: ' + name)
        expected = prefix(name)
        size = len(expected)
        if owner['effects'][:size] == expected:
            if 'lasting_state' not in parameters or replace_consumers(owner['effects'][size:], INPUTS) != owner['effects'][size:]:
                raise ValueError('An environmental attack consumer still reads caller flags: ' + name)
            if name != 'resolve_spell_multi_attack_target':
                sight_preconditions(owner, installed=True)
            continue
        if any(local in str(owner['effects']) for local in INPUTS.values()):
            raise ValueError('A partial environmental attack projection changed: ' + name)
        if 'lasting_state' not in parameters:
            owner['parameters'].append(_lasting())
        if name != 'resolve_spell_multi_attack_target':
            owner['pre_conditions'] = sight_preconditions(owner)
        owner['effects'] = expected + replace_consumers(owner['effects'], INPUTS)
        changed[name] = owner
    changed.update({name: owner for name, owner in nodes.items() if name not in runtime['tools']})
    runtime['tools'].update(changed)
    return list(changed)
