"""Name recorded area-spell saves without rerolls, HP changes or receipt rewriting."""
from copy import deepcopy
import json
import os
import zipfile

import pytest
import yaml

from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_vertical_cones import run, state_for
from scripts.srd_lite_visual_rolls import _lists


@pytest.fixture(scope='module')
def data():
    with zipfile.ZipFile(os.environ['SRD_LITE_CANDIDATE']) as archive:
        value = yaml.load(archive.read('packages/sylvia.srd-lite.bg3/runtime/ruleset.yaml'), Loader=yaml.CSafeLoader)
    if os.environ.get('SRD_LITE_PATCH_AREA_CAPTION') == '1':
        from scripts.srd_lite_visual_rolls import bind_area_save_source_caption
        bind_area_save_source_caption(value)
    return value


def test_every_selected_spell_has_its_authored_display_name(data):
    names = data['definitions']['srd_lite_spell_display_names']
    selected = data['definitions']['srd_lite_catalogue']
    assert len(selected) == 117
    assert all(isinstance(names[spell], str) and names[spell].strip() for spell in selected)
