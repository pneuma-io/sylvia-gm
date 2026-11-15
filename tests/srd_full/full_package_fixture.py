"""Bind preserved Full cases to Full without changing production Lite defaults."""

from sylvia_gm.application.bundled_packages import (
    _archive,
    bundled_rift_archive,
    bundled_rift_authority,
    bundled_rift_participant_declaration,
)
from sylvia_gm.application.srd_full_packages import srd_full_drafts as _srd_drafts


def bundled_srd_archive():
    # Read the fixture's local draft binding so existing monkeypatch cases stay local.
    return _archive(_srd_drafts())
