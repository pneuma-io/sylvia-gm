"""A reused immutable snapshot never reuses another Plan's admission result."""
import json
import pytest
from sylvia_gm.application.bundled_campaign_plans import bundled_plan
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.domain.package_library import canonical_json_bytes

@pytest.mark.parametrize("change", ["rule-hash", "closure-hash", "inline-secret", "act-reference"])
def test_exact_snapshot_reuse_still_rejects_changed_plan(change):
    selected=bundled_plan("rift-awakening")
    payload=json.loads(canonical_json_bytes(selected.plan.payload))
    assert validate_plan_snapshot(selected.package_snapshot,payload,"# Same Plan").payload==selected.plan.payload
    if change=="rule-hash":payload["bindings"]["ruleset"]["content_sha256"]="0"*64
    elif change=="closure-hash":payload["bindings"]["package_closure_sha256"]="0"*64
    elif change=="inline-secret":
        secret=payload["secrets"][0]
        payload["secrets"][0]={"id":secret["id"],"fact":"Changed secret", "known_by":[],"reveal":secret["reveal"]}
    else:payload["structure"][0]["setting_refs"].append("missing/never-authored.md")
    with pytest.raises(ValueError):validate_plan_snapshot(selected.package_snapshot,payload,"# Changed Plan")
