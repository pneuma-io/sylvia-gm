"""Meaningful Builder reads/edits and exact-source card confirmation."""
import json
import pytest
from sylvia_gm.agent._character_profile_tools import character_profile_tools
from sylvia_gm.application.character_profiles import save_character_profile,load_character_profile
from sylvia_gm.application.character_card_import import save_character_card_import,stage_card_preview,confirm_card_preview,load_import_original
from sylvia_gm.domain._content_schema_character_profile_v2 import EXAMPLE
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_rich_character_profiles import card

def test_builder_author_edit_sections_and_exact_import(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    tools={t.name:t for t in character_profile_tools(workspace,
        save=lambda data:save_character_profile(tmp_path,operation_id='tool',source=data).catalog_payload(),
        read=lambda id,sha:load_character_profile(tmp_path,id,sha),
        import_source=lambda data:save_character_card_import(tmp_path,operation_id='card',source=data))}
    def call(name,**params):return tools[name].handler(params,None)
    (workspace/'rich.json').write_bytes(canonical_json_bytes(EXAMPLE))
    checked=call('check_character_profile_draft',path='rich.json');assert checked['valid'] and not checked['published']
    saved=call('publish_character_profile',path='rich.json',expected_version=checked['version']);assert saved['success']
    params=dict(artifact_id=saved['id'],expected_content_sha256=saved['content_sha256'])
    lore=call('read_character_profile_section',**params,section='lore',entry_id='archive')
    assert lore['success'] and lore['content']['enabled'] is False and lore['played_history'] is False
    assert call('read_character_profile_section',**params,section='biography')['content']['voice']=='Dry and measured.'
    assert call('read_character_profile_section',**params,section='foreign_configuration')['foreign_behavior']=='preserved_only'
    assert call('read_character_profile_section',**params,section='lore',entry_id='absent')['present'] is False
    opened=call('open_character_profile',**params,destination_path='copy.json');assert opened['success']
    changed=json.loads((workspace/'copy.json').read_text());changed['biography']['voice']='Edited voice.'
    (workspace/'copy.json').write_bytes(canonical_json_bytes(changed))
    assert call('publish_character_profile',path='copy.json',expected_version=opened['version'])['code']=='stale_file'
    current=call('check_character_profile_draft',path='copy.json')
    edited=call('publish_character_profile',path='copy.json',expected_version=current['version']);assert edited['id']!=saved['id']
    assert load_character_profile(tmp_path,saved['id']).profile==EXAMPLE
    (workspace/'card.json').write_text(json.dumps(card(2)))
    preview=call('preview_character_card_import',path='card.json');assert preview['success'] and not preview['published']
    assert call('get_character_card_import_contract')['native_definition']=='character_profile_v2'
    assert call('import_character_card',path='card.json',expected_version='stale')['code']=='stale_file'
    imported=call('import_character_card',path='card.json',expected_version=preview['version']);assert imported['success'] and not imported['campaign_ready']
    assert call('preview_character_card_import',path='../elsewhere')['code']=='character_profile_path_denied'

def test_desktop_preview_captures_original_and_rejects_tamper(tmp_path):
    source=tmp_path/'card.json';raw=json.dumps(card(3)).encode();source.write_bytes(raw)
    preview=stage_card_preview(tmp_path,source)
    assert not (tmp_path/'character-profiles').exists()
    source.write_text('{}')
    saved=confirm_card_preview(tmp_path,preview['source_sha256'])
    assert saved['status']=='imported'
    assert load_import_original(tmp_path,preview['source_sha256'])==raw
    assert confirm_card_preview(tmp_path,preview['source_sha256'])==saved
    pending=tmp_path/'character-card-previews'/preview['source_sha256'];pending.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):confirm_card_preview(tmp_path,preview['source_sha256'])
