"""The bundled Lite inspector keeps its complete projection with the bounded loader."""

import yaml

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from tests.admitted_library_fixture import CANDIDATE_ID, prepare_library_database


def test_bundled_lite_inspector_matches_the_previous_safe_loader(tmp_path, monkeypatch):
    database = tmp_path / "inspector.sqlite3"
    prepare_library_database(database, bundled_srd_archive())
    original_load = yaml.load

    def previous_loader(stream, **kwargs):
        return original_load(stream, Loader=yaml.SafeLoader)

    with AuthorityOwner.open(database, "inspector-loader-comparison") as owner:
        with monkeypatch.context() as legacy:
            legacy.setattr(yaml, "load", previous_loader)
            before = owner.inspect_package_candidate(CANDIDATE_ID)
        after = owner.inspect_package_candidate(CANDIDATE_ID)
    assert after == before
