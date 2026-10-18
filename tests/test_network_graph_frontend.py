"""Host renderer wiring checks for bounded network graph surfaces."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "src-tauri" / "frontend"


def test_network_graph_is_host_owned_accessible_and_ruleset_neutral() -> None:
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")

    renderer = script.split("function renderNetworkGraphPanel(", maxsplit=1)[1]
    renderer = renderer.split("function wireNetworkGraph(", maxsplit=1)[0]
    assert 'aria-label="Visible network topology"' in renderer
    assert 'aria-label="Visible network links"' in renderer
    assert "data-network-node" in renderer
    assert "data-network-link" in renderer
    assert "data-network-function" in renderer
    assert "data-network-physical-surface" in renderer
    assert "surface.renderer === 'network_graph'" in script
    assert ".network-node" in css
    assert ".network-link" in css
    assert ".network-function" in css
    for forbidden in ("hack difficulty", "intrusion roll", "privilege calculation"):
        assert forbidden not in renderer.lower()


def test_graph_actions_send_only_declared_current_revision_selections() -> None:
    script = Path(__file__).with_name("frontend_network_actor_selection.test.cjs")
    completed = subprocess.run(
        ["node", "--test", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_cross_surface_navigation_uses_projected_identity_in_both_directions() -> None:
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")

    assert "data-network-physical-surface" in script
    assert "data-network-physical-entity" in script
    assert "data-network-return-surface" in script
    assert "data-network-return-node" in script
    assert "state.crossSurfaceFocus" in script
    assert "openBoundSurface(" in script
    assert "CSS.escape(button.dataset.networkPhysicalEntity)" in script
    assert "CSS.escape(button.dataset.networkReturnNode)" in script
