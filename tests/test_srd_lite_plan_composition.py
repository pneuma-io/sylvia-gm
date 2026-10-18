"""Admit all actual Plan snapshots, never relabel scripted structure as genuine play."""
import json
import os
from pathlib import Path

import pytest

from scripts.srd_lite_plan_composition import compose_lite_plans
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot


@pytest.fixture(scope='module')
def compiled_plans(tmp_path_factory):
    root = Path(os.environ['SRD_LITE_PLAN_OUTPUT']) if 'SRD_LITE_PLAN_OUTPUT' in os.environ else (
        tmp_path_factory.mktemp('lite-plans') / 'compiled')
    result = compose_lite_plans(Path(os.environ['SRD_LITE_CANDIDATE']),
        Path(__file__).parent / 'fixtures/lite-plans', root)
    return root, result


def test_all_sixteen_plans_have_exact_setting_closures_and_preserved_policies(compiled_plans):
    root, result = compiled_plans
    assert result['portable_plans'] == 16 and len(result['variants']) == 3
    assert all(row['portable_admission'] and row['policy_preserved'] and row['party_preserved']
               for row in result['rows'])
    assert len({row['bindings']['package_closure_sha256'] for row in result['rows']}) == 3
    assert result['installed_or_genuine_play_acceptance'] is False


def test_plan_cannot_reuse_bare_or_wrong_setting_closure(compiled_plans):
    root, _result = compiled_plans
    plan = json.loads((root / 'last-cart-through/plan.json').read_text())
    plan['bindings']['package_closure_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='bindings'):
        validate_plan_snapshot((root / 'last-cart-through/setting.sylvia-gm-package').read_bytes(),
            plan, (root / 'last-cart-through/plan.md').read_text())


def test_ordinary_bundled_chooser_uses_the_exact_portable_lite_snapshots(compiled_plans, monkeypatch):
    from sylvia_gm.application import bundled_campaign_plans as bundled
    root, result = compiled_plans
    monkeypatch.setattr(bundled, 'bundled_plans_root', lambda: root)
    bundled._catalogue.cache_clear()
    bundled._bundled_plan.cache_clear()
    try:
        public = bundled.bundled_plan_catalog()
        assert {row['id'] for row in public} == {row['id'] for row in result['rows']}
        assert len(public) == 16 and 'the-rift-awakening' not in {row['id'] for row in public}
        for row in result['rows']:
            selected = bundled.selected_campaign_plan(root / 'unused-library', row['id'])
            assert selected.plan.payload['bindings'] == row['bindings']
            assert selected.package_snapshot == (root / row['id'] / 'setting.sylvia-gm-package').read_bytes()
            assert selected.plan.payload['initial_state']['mechanics'][0]['namespace'] == 'sylvia.srd-lite.bg3'
        files = bundled.bundled_plan_files(root)
        assert len([path for path in files if path.name == 'setting.sylvia-gm-package']) == 16
        assert all((root / 'the-rift-awakening' / name) in files for name in ('plan.json', 'plan.md'))
    finally:
        bundled._catalogue.cache_clear()
        bundled._bundled_plan.cache_clear()


def test_non_snapshot_plan_keeps_the_existing_full_srd_source_path(tmp_path, monkeypatch):
    from sylvia_gm.application import bundled_campaign_plans as bundled
    file = tmp_path / 'setting-files/lore/fixture.md'
    file.parent.mkdir(parents=True)
    file.write_bytes(b'retained\r\nworld\r\n')
    expected = bundled.PlanSources(b'original-source-path', {'original': True})
    seen = []
    def original(additions):
        seen.append(additions)
        return expected
    monkeypatch.setattr(bundled, 'plan_sources', original)
    assert bundled.sources_for(tmp_path) is expected
    assert seen == [(('lore/fixture.md', b'retained\nworld\n'),)]
