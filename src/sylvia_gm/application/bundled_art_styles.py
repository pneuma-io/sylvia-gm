"""Resolve style defaults from the actual selected Setting, never a character's name."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from sylvia_gm.application.builder_references import candidate_for_references, launch_references
from sylvia_gm.application.bundled_character_art import _art_root
from sylvia_gm.application.image_hooks import MAX_IMAGE_BYTES, ImageData
from sylvia_gm.domain.art_styles import ArtStyle, catalogue_art_styles, parse_art_style
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import campaign_setting_entries

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


def _styles() -> tuple[ArtStyle, ...]:
    path = _art_root() / "character-art.json"
    if not path.is_file():
        return ()
    return catalogue_art_styles(json.loads(path.read_bytes()))


def selected_art_style(owner: AuthorityOwner, session_id: str) -> dict[str, Any] | None:
    """Use a campaign's selected closure or a Builder's validated captured sources."""
    context = owner.session_context(session_id)
    campaign_id = context.session.campaign_id
    if campaign_id is None:
        references = launch_references(context)
        candidate = candidate_for_references(owner, references)
        if candidate is None:
            return None
        settings = frozenset(
            entry.package_id for entry in campaign_setting_entries(candidate.package_lock)
        )
    else:
        lock = owner._store.current_package_lock(campaign_id).package_lock
        settings = frozenset(entry.package_id for entry in campaign_setting_entries(lock))
        try:
            construction = owner._store.campaign_construction_basis(campaign_id)
        except CommandRejected:
            construction = None
        selected = construction.canonical_input.get("setting_id") if construction else None
        if isinstance(selected, str):
            settings &= {selected}
    if len(settings) != 1:
        return None
    matches = [style for style in _styles() if settings.intersection(style.campaign_setting_ids)]
    return matches[0].as_payload() if len(matches) == 1 else None


def style_reference(value: object) -> ImageData:
    """Read only a declared content-addressed style image with matching original bytes."""
    style = parse_art_style(value)
    if style not in _styles():
        raise ValueError("The saved artwork style is unavailable; restore Sylvia's artwork library")
    root = _art_root().resolve()
    path = root / style.reference_path
    if not path.resolve().is_relative_to(root) or path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("The artwork style reference is invalid")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != style.reference_sha256:
        raise ValueError("The artwork style reference does not match its saved identity")
    return ImageData(raw)
