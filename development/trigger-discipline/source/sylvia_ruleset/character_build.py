"""Compile specialty eligibility from the canonical notes and check authored build choices."""

from collections import Counter
from pathlib import Path
from typing import Any

from balance_suite.specialties import parse_all_specialties


def eligibility_catalog(root: Path) -> dict[str, Any]:
    """Retain source identities without importing counterfactual outcomes or balance defaults."""
    nodes = parse_all_specialties(root)
    for node in nodes:
        class_text = (root / 'Classes' / f'{node.class_id}.md').read_text(encoding='utf-8-sig')
        if f'[[{node.specialty}]]' not in class_text:
            raise ValueError(f'{node.source_path}: class ownership needs reconciliation')
    return {
        'format': 1,
        'authority': ['Character/LEVEL.md', 'Character/SPECIALTY.md', 'Character/CLASS-SKILL INDEPENDENCE.md'],
        'nodes': [{
            'id': node.node_id, 'class_id': node.class_id, 'specialty': node.specialty,
            'tier': 0 if node.tier == 'ENTRY' else int(node.tier.split()[-1]),
            'source': node.source_path, 'source_sha256': node.source_sha256,
            'unresolved': 'UNRESOLVED' in node.name.upper(),
        } for node in nodes],
    }


def build_issues(participant: dict[str, Any], catalog: dict[str, Any]) -> list[str]:
    """Check choice budget, class and tier prerequisites; do not approve resource allocations."""
    issues = []
    level = participant.get('level')
    selected = participant.get('specialties')
    if type(level) is not int or not 1 <= level <= 20:
        return ['Choose a level from 1 to 20.']
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        return ['Specialties must be a list of node identities.']
    if len(selected) > level:
        issues.append(f'Level {level} grants at most {level} specialty choices; {len(selected)} are selected.')
    if len(set(selected)) != len(selected):
        issues.append('A specialty node cannot be selected more than once.')
    nodes = {node['id']: node for node in catalog['nodes']}
    counts = Counter((nodes[item]['specialty'], nodes[item]['tier']) for item in set(selected) if item in nodes)
    for identity in selected:
        node = nodes.get(identity)
        if node is None:
            issues.append(f'{identity}: not in this exact Ruleset catalog.')
            continue
        if node['unresolved']:
            issues.append(f'{identity}: the canonical node is unresolved.')
        if node['class_id'] != participant.get('class_id'):
            issues.append(f'{identity}: belongs to {node["class_id"]}.')
        tier = node['tier']
        if tier and counts[node['specialty'], tier - 1] < (1 if tier == 1 else 2):
            requirement = 'its ENTRY' if tier == 1 else f'two TIER {tier - 1} choices'
            issues.append(f'{identity}: requires {requirement} in {node["specialty"]}.')
    return issues


def eligibility_constraints(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Encode settled prerequisites in the ordinary Character File schema, without stat defaults."""
    nodes = [node for node in catalog['nodes'] if not node['unresolved']]

    def fields(**properties: dict[str, Any]) -> dict[str, Any]:
        return {'type': 'object', 'required': list(properties), 'properties': properties}

    def choices(**constraints: Any) -> dict[str, Any]:
        return {'type': 'array', 'items': {'type': 'string'}, **constraints}

    constraints = [{
        'type': 'object',
        'if': fields(level={'type': 'integer', 'enum': [level]}),
        'then': fields(specialties=choices(maxItems=level)),
    } for level in range(1, 21)]
    for class_id in sorted({node['class_id'] for node in nodes}):
        constraints.append({
            'type': 'object',
            'if': fields(class_id={'type': 'string', 'enum': [class_id]}),
            'then': fields(specialties=choices(items={
                'type': 'string', 'enum': [node['id'] for node in nodes
                                         if node['class_id'] == class_id],
            })),
        })
    for specialty in sorted({node['specialty'] for node in nodes}):
        for tier in range(1, 6):
            current = [node['id'] for node in nodes
                       if node['specialty'] == specialty and node['tier'] == tier]
            previous = [node['id'] for node in nodes
                        if node['specialty'] == specialty and node['tier'] == tier - 1]
            if current:
                constraints.append({
                    'type': 'object',
                    'description': f'{specialty} TIER {tier} requires prior investment.',
                    'if': fields(specialties=choices(contains={'type': 'string', 'enum': current})),
                    'then': fields(specialties=choices(
                        contains={'type': 'string', 'enum': previous},
                        minContains=1 if tier == 1 else 2,
                    )),
                })
    return constraints
