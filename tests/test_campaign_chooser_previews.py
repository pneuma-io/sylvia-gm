"""Player-facing built-in previews must not disclose later plot decisions or hidden obstacles."""
import json
from pathlib import Path
from sylvia_gm.application.bundled_campaign_plans import read_plan_catalogue, bundled_plan

ROOT=Path(__file__).resolve().parents[1]/'campaign-plans'
FORBIDDEN={
 'rift-awakening':['deciding whether to stay','seek a way home','city of Nexus'],
 'the-long-way-round':['raider-held wayhouse'],
 'a-light-on-breathing-street':['hostile Rift echoes','trapped rooms'],
 'the-honest-thief':['money and a witness','dangerous exit'],
 'the-company-of-the-lost':['flooded records depot','contested Warden vault'],
 'the-map-of-tomorrow':['guarded causeway'],
 'the-cartographers-wake':['sealed survey vault'],
 'a-debt-in-iron':['then defend the crew','deciding how to face'],
 'the-bell-below':['hungry inhabitants'],
 'the-scavengers-accord':['open its dangerous vault']
}

def test_actual_bundled_chooser_projection_uses_public_opening_preview():
 rows=read_plan_catalogue(ROOT)
 assert len(rows)==16
 for row in rows:
  plan=bundled_plan(row['id'])
  assert plan.summary==row['pitch']
  assert len(plan.summary.strip())>=60
  for spoiler in FORBIDDEN.get(row['id'],[]):assert spoiler.lower() not in plan.summary.lower()
 rift=bundled_plan('rift-awakening')
 assert 'woodland clearing' in rift.summary and 'Thornwall' in rift.summary
 # Public preview edits retain the authored GM ending rather than deleting the decision.
 gm=json.dumps(dict(rift.plan.payload),default=str).lower()
 assert 'ending' in gm and ('home' in gm or 'return' in gm)


def test_retained_exact_story_copy_uses_safe_hook_without_rebinding_or_editing():
 from copy import deepcopy
 from dataclasses import replace
 from sylvia_gm.application.bundled_campaign_plans import known_builtin_public_summary
 from sylvia_gm.domain.authority import owned_payload, thaw_json_value
 original=bundled_plan('rift-awakening')
 payload=thaw_json_value(original.plan.payload)
 payload['bindings']['ruleset']['content_sha256']='a'*64
 retained=replace(original.plan,payload=owned_payload(payload))
 before=deepcopy(dict(retained.payload))
 assert known_builtin_public_summary(retained)==original.summary
 assert dict(retained.payload)==before
 edited=deepcopy(payload);edited['premise']='A player-authored alternate adventure.'
 assert known_builtin_public_summary(replace(retained,payload=owned_payload(edited))) is None
 assert known_builtin_public_summary(replace(retained,readable_markdown=retained.readable_markdown+'\nCustom GM notes.')) is None

