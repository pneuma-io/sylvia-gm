"""Select hash-bound complete final bytes without weakening native admission."""

import hashlib
import json
import os
from pathlib import Path

from tests.srd_full import test_srd_magic_jar as jar
from tests.srd_full import test_srd_public_spell_casting as public


def qualified_runtime(monkeypatch):
    if os.environ.get("FC14_RECEIVER_MANIFEST"):
        from candidate_reader import qualified_sim_runtime
        yield from qualified_sim_runtime(monkeypatch)
        return
    manifest_path = Path(os.environ["FC14_ACTIONS1_COMPOSITION"])
    manifest = json.loads(manifest_path.read_text())
    assert manifest["ticket_id"] == "00000000-0000-4000-8000-000000000001"
    assert manifest["parent_runtime_sha256"] == (
        "3540dec3751e7f150584e86bf4329f1110d84be21a53500ec88215d3b3ef8c47"
    )
    source = Path(manifest["final_root"])
    package = source / "rulesets/dnd-srd-5.2.1"
    for binding in manifest["generated"]:
        assert (
            hashlib.sha256((source / binding["path"]).read_bytes()).hexdigest() == binding["sha256"]
        )
    assert manifest["observations"]["native_at_capture"]
    assert manifest["observations"]["resolved_parameter_equality"]
    assert manifest["observations"]["expanded_nodes"] <= 500000
    # Public creation reads the same complete ordinary package root. No draft
    # truncation, admission waiver, synthetic archive, or preinstallation bypass.
    monkeypatch.setattr(public, "ROOT", source)
    archive = Path(manifest["archive"]["path"])
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["archive"]["sha256"]
    monkeypatch.setattr(public, "bundled_srd_archive", archive.read_bytes)
    # The cards helper reads headers relative to its source __file__. Bind that
    # data consumer to the same complete admitted inventory as the runtime.
    inventory = json.loads((package / "content/spell-status-inventory.json").read_bytes())
    headers = {row["id"]: row["casting_requirements"] for row in inventory["spells"]}
    monkeypatch.setattr(jar.cards, "_casting_headers", lambda: headers)
    monkeypatch.setenv("FC14_JAR_RUNTIME_ROOT", str(package))
    # The existing receiver selects real tool definitions and deterministic dice;
    # no admission/declaration/owner replacement is used by the public tests.
    yield from jar._compiled.__wrapped__(monkeypatch)
