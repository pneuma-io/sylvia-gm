"""Host-renderer wiring checks for the built-in tactical grid."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "src-tauri" / "frontend"


def test_tactical_grid_is_host_owned_accessible_and_ruleset_neutral() -> None:
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")

    assert "function renderTacticalGridPanel(" in script
    assert "surface.renderer === 'tactical_grid'" in script
    assert 'aria-label="Tactical grid; drag to pan, arrow keys move cell focus"' in script
    assert "ArrowLeft" in script and "ArrowRight" in script
    assert "data-tactical-zoom" in script
    assert "data-tactical-entity" in script
    assert "data-tactical-cell" in script
    assert "scroll.addEventListener('pointerdown'" in script
    assert "scroll.addEventListener('pointermove'" in script
    assert "scroll.scrollLeft = pan.scrollLeft - deltaX" in script
    assert "scroll.scrollTop = pan.scrollTop - deltaY" in script
    assert ".tactical-scroll.panning" in css
    pan_wiring = script.split("let pan = null;", maxsplit=1)[1]
    pan_wiring = pan_wiring.split(
        "panel.querySelectorAll('[data-tactical-entity]')", maxsplit=1
    )[0]
    pointer_down = pan_wiring.split("scroll.addEventListener('pointermove'", maxsplit=1)[0]
    assert "setPointerCapture" not in pointer_down
    assert pan_wiring.index("Math.hypot(deltaX, deltaY) < 4") < pan_wiring.index(
        "scroll.setPointerCapture(event.pointerId)"
    )
    assert ".tactical-world" in css
    assert ".tactical-door" in css
    assert ".tactical-cover" in css
    assert ".tactical-wall" in css
    assert ".tactical-zone" in css
    assert ".tactical-objective" in css
    for assumption in ("armor class", "saving throw", "spell slot", "hit points"):
        assert assumption not in script.lower()


def test_tactical_preview_uses_owner_projection_without_client_estimates() -> None:
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    preview = script.split("function tacticalMapPreviewDetails(", maxsplit=1)[1]
    preview = preview.split("function renderTacticalGridPanel(", maxsplit=1)[0]

    assert "mapPreview.path?.points" in preview
    assert "mapPreview.ray?.contacts" in preview
    assert "mapPreview.exposure?.cover_ids" in preview
    assert "mapPreview.crossed_zones" in preview
    assert "tacticalClientPreview" not in script
    assert "tacticalLineCells" not in script
    assert "execute_panel_action" not in preview


def test_dynamic_panel_intent_rechecks_authority_and_sends_only_declared_selection() -> None:
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    binder = script.split("function tacticalActionSelection(", maxsplit=1)[1]
    binder = binder.split("function panelContent(", maxsplit=1)[0]
    handler = script.split("function renderRulesetPanelProjection(", maxsplit=1)[1]
    handler = handler.split("container.querySelectorAll('[data-panel-action-id]')", maxsplit=1)[1]
    handler = handler.split("container.querySelectorAll('[data-panel-size-id]')", maxsplit=1)[0]

    assert "Object.values(bindings)" in binder
    assert "selected[source]" in binder
    assert "if (source === 'path') return" in binder
    assert "map_revision" in binder
    assert "const campaignId = state.activeCampaignId" in handler
    assert "await state.bridge.snapshot(campaignId)" in handler
    assert "panelActionSelection(surface, action, button.dataset.panelActorId)" in handler
    assert "expected_revision: actionSnapshot.revision" in handler
    assert "selection," in handler
    assert "await renderRulesetPanels(campaignId, true)" in handler
    assert "session_id: sessionId" in handler


def test_tauri_bridge_carries_selection_without_exposing_an_authority_bypass() -> None:
    rust = (ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    command = rust.split("fn execute_panel_action(", maxsplit=1)[1]
    command = command.split("#[tauri::command]", maxsplit=1)[0]

    assert "selection: Option<Value>" in command
    assert '"selection": selection' in command
    assert '"session_id": session_id' in command
    assert '"execute_panel_action"' in command
    assert "sqlite" not in command.lower()
