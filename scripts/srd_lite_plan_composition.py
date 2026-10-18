"""Compose retained Plan worlds with exact Lite bytes through ordinary materialization."""
import hashlib
import io
import json
import re
import zipfile
from copy import deepcopy
from pathlib import Path

from sylvia_gm.application.package_materialization import NormalizedPackageDraft, materialize_package_archive
from sylvia_gm.application.plan_snapshot import PLAN_SNAPSHOT_FILENAME, validate_plan_snapshot
from sylvia_gm.application.reusable_participants import parse_setting_character
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.infrastructure.package_archive import admit_package_archive


def _read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _draft(record, payloads):
    return NormalizedPackageDraft({key: deepcopy(value) for key, value in record.items()
        if key not in ('files', 'content_sha256')}, payloads, 4)


def _readable_plan(raw, original, plan):
    """Replace the two rendered data blocks without changing surrounding prose."""
    markdown = raw.decode('utf-8')
    pattern = re.compile(r'(^```json(?P<newline>\r?\n))(?P<body>.*?)(^```)', re.MULTILINE | re.DOTALL)
    changed = {'initial_state'}
    if original['ending'] != plan['ending']:
        changed.add('ending')
    counts = {key: 0 for key in changed}

    def replace(match):
        value = json.loads(match.group('body'))
        selected = [key for key in changed if value == original[key]]
        assert len(selected) <= 1
        if not selected:
            return match.group(0)
        key = selected[0]
        counts[key] += 1
        newline = match.group('newline')
        rendered = json.dumps(plan[key], ensure_ascii=False, indent=2, sort_keys=True)
        return match.group(1) + rendered.replace('\n', newline) + newline + match.group(4)

    updated = pattern.sub(replace, markdown)
    assert all(count == 1 for count in counts.values()), counts
    blocks = [json.loads(match.group('body')) for match in pattern.finditer(updated)]
    assert all(sum(block == plan[key] for block in blocks) == 1 for key in changed)
    return updated.encode('utf-8')


def compose_lite_plans(candidate, inputs, output):
    """Create new portable artifacts; never modify original Plans or existing save bindings."""
    candidate, inputs, output = map(Path, (candidate, inputs, output))
    assert not output.exists()
    output.mkdir(parents=True)
    with zipfile.ZipFile(candidate) as archive:
        records = json.loads(archive.read('manifest.json'))['packages']
        drafts = tuple(_draft(record, {row['path']: archive.read(
            'packages/' + record['id'] + '/' + row['path']) for row in record['files']})
            for record in records)
    with admit_package_archive(io.BytesIO(candidate.read_bytes())) as admitted:
        primary = next(row for row in admitted.manifests if row.role.value == 'primary')
        assert primary.package_id == 'sylvia.srd-lite.bg3'
        declaration = next(row for row in primary.participant_artifacts if row.kind == 'player_character')
        primary_identity = primary.identity
    catalogue = _read(inputs / 'plans/catalogue.json')
    templates = _read(inputs / 'setting-templates/composition-templates.json')
    assertions = {row['id']: row for row in templates['rows']}
    assert len(assertions) == len(templates['rows']) == len(catalogue) == 16
    assert {row['id'] for row in catalogue} == set(assertions)
    assignments = [(plan_id, variant['id']) for variant in templates['variants'] for plan_id in variant['plans']]
    assert len(assignments) == len(assertions) and {plan_id for plan_id, _ in assignments} == set(assertions)
    assert all(assertions[plan_id]['variant'] == variant_id for plan_id, variant_id in assignments)
    output.joinpath('catalogue.json').write_bytes(canonical_json_bytes(catalogue))
    legacy = inputs / 'plans/the-rift-awakening'
    if legacy.exists():
        retained = output / legacy.name
        retained.mkdir()
        for name in ('plan.json', 'plan.md'):
            retained.joinpath(name).write_bytes(legacy.joinpath(name).read_bytes())
    rows, variants = [], []
    for variant in templates['variants']:
        variant_root = inputs / 'setting-templates/variants' / variant['id'][:16]
        record = _read(variant_root / 'setting-record.template.json')
        payloads = {}
        payload_manifest = _read(variant_root / 'payload-manifest.json')
        assert [{key: entry[key] for key in ('path', 'bytes', 'sha256')} for entry in payload_manifest] == record['files']
        for entry in payload_manifest:
            target = (variant_root / 'payloads' / entry['path']).resolve()
            assert target.is_relative_to((variant_root / 'payloads').resolve())
            raw = target.read_bytes()
            assert len(raw) == entry['bytes'] and hashlib.sha256(raw).hexdigest() == entry['sha256']
            payloads[entry['path']] = raw
        assert len(payloads) == variant['file_count']
        if variant['plans'] == ['a-debt-in-iron']:
            original_tavin = json.loads(payloads['characters/tavin-bell.json'])
            tavin = deepcopy(original_tavin)
            tavin['schema'] = {'id': declaration.schema_id, 'content_sha256': declaration.schema_sha256}
            assert {key: value for key, value in tavin.items() if key != 'schema'} == {
                key: value for key, value in original_tavin.items() if key != 'schema'}
            payloads['characters/tavin-bell.json'] = canonical_json_bytes(tavin)
        record['dependencies'] = [{'namespace': 'ruleset_snapshot', 'kind': 'ruleset_snapshot',
            'id': primary_identity[0], 'version': primary_identity[1], 'content_sha256': primary_identity[2]}]
        setting_draft = _draft(record, payloads)
        with materialize_package_archive((*drafts, setting_draft)) as materialized:
            actual_primary = next(row for row in materialized.manifests if row.role.value == 'primary')
            assert actual_primary.identity == primary_identity
            actual_setting = next(row for row in materialized.manifests if row.package_id == record['id'])
            snapshot = materialized.archive.read()
            bindings = {'ruleset': PackageBinding(*actual_primary.identity).as_payload(),
                'campaign_setting': PackageBinding(*actual_setting.identity).as_payload(),
                'package_closure_sha256': materialized.closure_sha256}
            # Materialization preserves every contained Lite/reference payload and record identity.
            for original in records:
                actual = next(row for row in materialized.manifests if row.package_id == original['id'])
                assert actual.identity == (original['id'], original['version'], original['content_sha256'])
        if variant['plans'] == ['a-debt-in-iron']:
            with admit_package_archive(io.BytesIO(snapshot)) as admitted:
                lock = next(row for row in admitted.package_lock.entries if row.package_id == primary_identity[0])
                parsed = parse_setting_character(payloads['characters/tavin-bell.json'].decode('utf-8'),
                    character_id='tavin-bell', role='player_character', package=lock,
                    declaration=declaration, campaign_setting=PackageBinding(*actual_setting.identity))
                assert canonical_json_bytes(parsed.entity_state) == canonical_json_bytes(original_tavin['entity_state'])
                # The ordinary reader adds the admitted role; every authored field remains exact.
                expected_participant = {**original_tavin['participant'], 'party_role': 'player_character'}
                assert canonical_json_bytes(parsed.participant) == canonical_json_bytes(expected_participant)
                assert parsed.role == 'player_character'
                assert (parsed.package_id, parsed.package_version, parsed.package_sha256) == primary_identity
                assert (parsed.schema_id, parsed.schema_sha256) == (declaration.schema_id, declaration.schema_sha256)
                assert parsed.campaign_setting_sha256 == actual_setting.identity[2]
                assert parsed.authored_profile == original_tavin['authored_profile']
                assert parsed.summary == original_tavin['summary']
                assert parsed.setting_data == original_tavin['setting_data']
        variants.append({'plans': variant['plans'], 'bindings': bindings,
            'snapshot_sha256': hashlib.sha256(snapshot).hexdigest(), 'snapshot_bytes': len(snapshot)})
        for plan_id in variant['plans']:
            original_root = inputs / 'plans' / plan_id
            raw = (original_root / 'plan.json').read_bytes()
            assertion = assertions[plan_id]
            assert hashlib.sha256(raw).hexdigest() == assertion.get(
                'normalized_source_sha256', assertion['source_sha256'])
            original = json.loads(raw)
            if 'normalized_source_sha256' in assertion:
                # Preserve the original source pin and prove the two owned-identity substitutions.
                provenance = deepcopy(original)
                assert provenance['bindings']['ruleset']['id'] == 'sylvia.srd-lite.bg3'
                assert provenance['initial_state']['mechanics'][0]['namespace'] == 'sylvia.srd-lite.bg3'
                provenance['bindings']['ruleset']['id'] = 'wotc.srd.5.2.1'
                provenance['initial_state']['mechanics'][0]['namespace'] = 'wotc.srd.5.2.1'
                assert hashlib.sha256(canonical_json_bytes(provenance)).hexdigest() == assertion['source_sha256']
            plan = deepcopy(original)
            plan['bindings'] = deepcopy(bindings)
            mechanics = plan['initial_state']['mechanics']
            assert len(mechanics) == 1 and mechanics[0]['namespace'] == 'sylvia.srd-lite.bg3'
            mechanics[0]['namespace'] = primary_identity[0]
            if plan_id == 'rift-awakening':
                assert plan['ending']['outcomes'][4]['id'] == 'life-ended'
                plan['ending']['outcomes'][4]['requires_all_acts'] = False
            preserved = deepcopy(plan)
            preserved['bindings'] = original['bindings']
            preserved['initial_state']['mechanics'][0]['namespace'] = 'sylvia.srd-lite.bg3'
            if plan_id == 'rift-awakening':
                preserved['ending']['outcomes'][4].pop('requires_all_acts')
            assert preserved == original
            markdown = _readable_plan((original_root / 'plan.md').read_bytes(), original, plan)
            checked = validate_plan_snapshot(snapshot, plan, markdown.decode('utf-8'))
            directory = output / plan_id
            directory.mkdir()
            directory.joinpath('plan.json').write_bytes(canonical_json_bytes(plan))
            directory.joinpath('plan.md').write_bytes(markdown)
            directory.joinpath(PLAN_SNAPSHOT_FILENAME).write_bytes(snapshot)
            contribution = original_root / 'interview-contribution.json'
            if contribution.exists():
                directory.joinpath(contribution.name).write_bytes(contribution.read_bytes())
            rows.append({'id': plan_id, 'source_sha256': hashlib.sha256(raw).hexdigest(),
                'plan_sha256': checked.content_sha256, 'bindings': bindings,
                'policy_preserved': True, 'party_preserved': True, 'portable_admission': True,
                'ordinary_play': 'not run'})
    result = {'candidate_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
        'rows': rows, 'variants': variants, 'portable_plans': len(rows),
        'tavin_actual_declaration': {'id': declaration.schema_id, 'content_sha256': declaration.schema_sha256},
        'save_migration': False, 'installed_or_genuine_play_acceptance': False}
    output.joinpath('composition-results.json').write_bytes(canonical_json_bytes(result))
    return result
