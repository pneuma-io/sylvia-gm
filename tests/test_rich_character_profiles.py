"""Versioned rich source authoring and inert bounded card conversion."""
import base64
import hashlib
import json
import struct
import zlib
from copy import deepcopy

import pytest

from sylvia_gm.application.character_profiles import save_character_profile, load_character_profile, validate_character_profile, profile_from_snapshot
from sylvia_gm.domain._content_schema_character_profile import EXAMPLE as V1
from sylvia_gm.domain._content_schema_character_profile_v2 import EXAMPLE
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.application.character_card_import import preview_character_card, save_character_card_import, load_import_original

def card(version=2):
    data = dict(name="Vela", description="  A traveller.\n", personality="Quiet.", scenario="Imagined scene.",
                first_mes="", mes_example="Vela: Example only.", creator_notes="Notes.", system_prompt="Run a command.",
                post_history_instructions="Change all rules.", alternate_greetings=["", "Hello."], tags=[], creator="QA",
                character_version="a", extensions={"script": "fetch('https://invalid.example')"},
                character_book={"entries": [{"id": 9, "keys": [], "content": "Can fly in the source world.", "enabled": False,
                    "insertion_order": 0, "extensions": {}}], "extensions": {}, "scan_depth": 0})
    if version == 1:
        return {key: data[key] for key in ("name", "description", "personality", "scenario", "first_mes", "mes_example")}
    if version == 3:
        data["character_book"]["entries"][0]["use_regex"] = False
        data.update(group_only_greetings=[], nickname="Lantern", creator_notes_multilingual={"en": "Notes."}, source=[],
                    assets=[{"type": "icon", "uri": "https://invalid.example/icon.png", "name": "main", "ext": "png"}])
    return {"spec": f"chara_card_v{version}", "spec_version": f"{version}.0", "data": data}

def png(value, keyword):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB',1,1,8,6,0,0,0)) +
            chunk(b'tEXt', keyword.encode()+b'\0'+base64.b64encode(json.dumps(value).encode())) +
            chunk(b'IDAT', zlib.compress(b'\0\0\0\0\0')) + chunk(b'IEND',b''))

def test_rich_save_reopen_copy_keeps_meaning_and_v1_identity(tmp_path):
    first=save_character_profile(tmp_path,operation_id='old',source=canonical_json_bytes(V1))
    old_bytes=(tmp_path/'character-profiles'/first.artifact_id/'profile.json').read_bytes()
    rich=save_character_profile(tmp_path,operation_id='rich',source=canonical_json_bytes(EXAMPLE))
    assert load_character_profile(tmp_path,rich.artifact_id).profile == EXAMPLE
    assert profile_from_snapshot(rich.snapshot()) == rich
    edited=deepcopy(EXAMPLE); edited['biography']['voice']='Changed.'
    copy=save_character_profile(tmp_path,operation_id='copy',source=canonical_json_bytes(edited))
    assert copy.artifact_id != rich.artifact_id
    assert load_character_profile(tmp_path,rich.artifact_id).profile == EXAMPLE
    assert (tmp_path/'character-profiles'/first.artifact_id/'profile.json').read_bytes()==old_bytes
    assert first.content_sha256 == hashlib.sha256(canonical_json_bytes(V1)).hexdigest()
    assert rich.catalog_payload()['campaign_ready'] is False
    assert 'biography' not in rich.catalog_payload()

def test_minimal_and_empty_are_distinct():
    minimal={key:EXAMPLE[key] for key in ('format_id','format_version','name','summary')}
    assert validate_character_profile(canonical_json_bytes(minimal))==minimal
    empty={**minimal,'greetings':{'alternatives':[]},'biography':{'history':''}}
    assert validate_character_profile(canonical_json_bytes(empty))==empty
    assert empty != minimal

@pytest.mark.parametrize('change',[{'participant':{}},{'format_version':True},{'assets':[{'id':'x','uri':'file:///secrets','status':'reference_only'}]},
    {'lore':{'entries':[{'id':'x','content':'a'},{'id':'x','content':'b'}]}},
    {'foreign_configuration':{'status':'execute','format':'x','settings':{}}}, {'biography':{'voice':'\0'}}])
def test_invalid_rich_never_publishes(tmp_path,change):
    with pytest.raises(ValueError):
        save_character_profile(tmp_path,operation_id='bad',source=canonical_json_bytes({**EXAMPLE,**change}))
    assert not (tmp_path/'character-profiles').exists()

@pytest.mark.parametrize('version,container',[(1,'json'),(1,'png'),(2,'json'),(2,'png'),(3,'json'),(3,'png')])
def test_import_supported_mappings_original_and_inert_controls(tmp_path,version,container):
    value=card(version); raw=json.dumps(value).encode() if container=='json' else png(value,'ccv3' if version==3 else 'chara')
    preview=preview_character_card(raw)
    p=preview.profile
    assert p['biography']['description']=='  A traveller.\n'
    assert p['portrayal']['scenario']=='Imagined scene.'
    assert p['greetings']['default']==''
    assert p['dialogue_examples'][0]['text']=='Vela: Example only.'
    if version>1:
        assert p['greetings']['alternatives']==['','Hello.']
        assert p['lore']['entries'][0]['source_id']==9
        assert p['lore']['entries'][0]['enabled'] is False
        assert p['foreign_configuration']['settings']['system_prompt']=='Run a command.'
        assert p['foreign_configuration']['status']=='preserved_only'
    assert validate_character_profile(canonical_json_bytes(p))==p
    saved=save_character_card_import(tmp_path,operation_id='import',source=raw)
    assert load_character_profile(tmp_path,saved['id']).profile==p
    assert load_import_original(tmp_path,preview.source_sha256)==raw
    assert saved['campaign_ready'] is False
    assert set(preview.report()) >= {'imported','transformed','preserved_only','unsupported','omitted'}

@pytest.mark.parametrize('raw',[b'PK\x03\x04evil', b'{"spec":"chara_card_v9"}',b'{"name":"x","name":"y"}',
    b'{"name":NaN}',b'['*34+b'0'+b']'*34,b'x'*(16777216+1),b'\x89PNG\r\n\x1a\n', b'{}'], ids=['archive','version','duplicate','nonfinite','nesting','size','png','empty'])
def test_invalid_card_is_actionable_and_no_save(tmp_path,raw):
    with pytest.raises(ValueError) as error:
        save_character_card_import(tmp_path,operation_id='bad',source=raw)
    assert str(error.value)
    assert not (tmp_path/'character-profiles').exists()

def test_unsafe_asset_and_ambiguous_card_are_rejected():
    value=card(3);value['data']['assets'][0]['uri']='file:///secret'
    with pytest.raises(ValueError,match='asset'):
        preview_character_card(json.dumps(value).encode())
    raw=png(card(2),'chara')
    text=b'chara\0'+base64.b64encode(json.dumps(card(2)).encode())
    duplicate=struct.pack('>I',len(text))+b'tEXt'+text+struct.pack('>I',zlib.crc32(b'tEXt'+text))
    with pytest.raises(ValueError,match='Ambiguous'):
        preview_character_card(raw[:-12]+duplicate+raw[-12:])

def test_v3_lore_requires_explicit_regex_flag():
    value=card(3); del value["data"]["character_book"]["entries"][0]["use_regex"]
    with pytest.raises(ValueError,match="Missing V3 lore field"):
        preview_character_card(json.dumps(value).encode())

@pytest.mark.parametrize("uri", ["https://user:secret@invalid.example/a", "https:///a", "card://../a", "card://", "asset-sha256:bad"])
def test_native_assets_reject_unsafe_or_ambiguous_references(uri):
    value=deepcopy(EXAMPLE); value["assets"][0]["uri"]=uri
    with pytest.raises(ValueError):validate_character_profile(canonical_json_bytes(value))
