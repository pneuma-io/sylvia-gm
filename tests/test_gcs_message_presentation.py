"""Selected mixed-message and interruption presentation, using synthetic campaign data."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import PlayerProjectionKind, WriterRole
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_alpha_f6c_canonical_frontend import FRONTEND_DIR, _action, _stand_up


@pytest.mark.parametrize("saved", [False, True])
def test_interruption_renders_once_with_truthful_copy_on_repaint(saved: bool) -> None:
    """Exercise the real DM renderer, including its terminal/projection join."""

    script = r"""
const fs=require('fs'),assert=require('assert');
const nodes=new Map();
global.window={};
global.document={addEventListener(){},getElementById(id){
  if(!nodes.has(id))nodes.set(id,{});return nodes.get(id);
}};
let source=fs.readFileSync(process.argv[1],'utf8');
source=source.replace('  // ---- Public API ----',`
  updateChronology=(container,markup)=>{container.innerHTML=markup;};
  renderSessionModelState=()=>{};
  refreshSessionInputAdmission=()=>{};
  window.testState=state;
  window.testRenderDMSession=renderDMSession;
  // ---- Public API ----`);
eval(source);
const saved=JSON.parse(process.argv[2]);
const terminal={kind:'session_attempt_terminal',attempt_id:'attempt-test',
  status:'interrupted',failure_category:'provider_tool_round_budget_exceeded'};
window.testState.activeSessionId='session-test';
window.testState.bridge={
  session_context:async()=>({messages:[{message_id:'terminal-test',kind:'evidence',content:terminal}]}),
  player_projection:async()=>[{kind:'retry',safe_payload:{
    interruption_id:'interruption-test',failed_attempt_id:'attempt-test',saved_changes:saved}}],
};
(async()=>{let previous;
  for(let paint=0;paint<2;paint++){
    await window.testRenderDMSession('campaign-test');
    const html=nodes.get('chronology').innerHTML;
    assert.equal((html.match(/story-card retry-card/g)||[]).length,1);
    assert(html.includes(saved
      ? "Game changes were saved, but Sylvia couldn't finish the narration."
      : "No game changes were saved for this turn."));
    assert(!html.includes('without a committed result'));
    if(previous)assert.equal(html,previous);
    previous=html;
  }
})();
"""
    subprocess.run(
        ["node", "-e", script, str(FRONTEND_DIR / "app.js"), json.dumps(saved)],
        check=True, capture_output=True, text=True,
    )


@pytest.mark.parametrize("kind", ["none", "mechanics", "campaign_file", "prior_notes"])
def test_saved_notice_uses_this_inputs_commits_and_survives_reopen(
    tmp_path: Path, kind: str
) -> None:
    database = tmp_path / "saved-notice.sqlite3"
    supervisor, bus, campaign_id = _stand_up(database)
    try:
        owner = supervisor._owner
        assert owner is not None
        owner.create_agent_session(
            campaign_id=campaign_id, session_id="session-notice", role=WriterRole.DM,
            provider_id="synthetic", model_id="synthetic",
        )
        owner.append_agent_message(
            session_id="session-notice", message_id="prior-input",
            kind=SessionMessageKind.USER, content={"text": "Earlier action"},
        )
        if kind == "prior_notes":
            owner.record_campaign_scene(
                session_id="session-notice", scene_id="prior-scene", act_id=None,
                summary="Earlier saved scene", location_id=None,
                basis_message_ids=("prior-input",), turn_id="turn-f6c",
            )
        before = owner.snapshot(campaign_id)
        owner.append_agent_message(
            session_id="session-notice", message_id="current-input",
            kind=SessionMessageKind.USER,
            content={"text": "Current action", "attempt_id": "attempt-notice",
                     "turn_id": "turn-f6c",
                     "canonical_start_sequence": before.events[-1].sequence},
        )
        owner.start_narration_attempt(
            campaign_id=campaign_id, attempt_id="attempt-notice", turn_id="turn-f6c",
            cancel=lambda: None,
        )
        if kind == "mechanics":
            # No provider contract and no bound last action: prove the automatic-transition
            # sequence window, excluding the fixture's earlier action in the same TURN.
            owner.execute_action(_action(
                campaign_id, action_id="current-action", owner_id="owner-f6c",
                revision=before.revision,
            ))
        elif kind == "campaign_file":
            owner.record_campaign_scene(
                session_id="session-notice", scene_id="corrected-scene", act_id=None,
                summary="No time passed; the original scene is corrected", location_id=None,
                basis_message_ids=("current-input",), turn_id="turn-f6c",
            )
        owner.interrupt_narration(campaign_id, "attempt-notice")
        snapshot = owner.snapshot(campaign_id)
        expected = kind in {"mechanics", "campaign_file"}
        notices = [p for p in bus.player_projection(campaign_id)
                   if p.kind is PlayerProjectionKind.RETRY]
        assert len(notices) == 1
        assert notices[0].safe_payload["saved_changes"] is expected
        assert owner.snapshot(campaign_id) == snapshot
    finally:
        supervisor.shutdown()
    with AuthorityOwner.open(database, "owner-reopened") as reopened:
        notices = [p for p in reopened.player_projection(campaign_id)
                   if p.kind is PlayerProjectionKind.RETRY]
        assert len(notices) == 1
        assert notices[0].safe_payload["saved_changes"] is expected
        assert reopened.snapshot(campaign_id) == snapshot


def test_response_blocks_keep_order_safe_text_and_legacy_fallback() -> None:
    blocks = [
        {"kind": "narration", "text": "The door opens."},
        {"kind": "npc_dialogue", "speaker_label": "Chloe", "text": "Come in."},
        {"kind": "npc_dialogue", "speaker_label": "<hidden>", "text": "<script>bad()</script>"},
        {"kind": "ooc", "text": "Saved your preference."},
    ]
    script = (
        "global.window={};global.document={addEventListener(){}};"
        f"require({json.dumps(str(FRONTEND_DIR / 'app.js'))});"
        f"const blocks={json.dumps(blocks)};"
        "process.stdout.write(JSON.stringify(["
        "window.SylviaGMFrontend.renderResponseBlocks(blocks,''),"
        "window.SylviaGMFrontend.renderResponseBlocks(null,'Old prose')]));"
    )
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    html, legacy = json.loads(result.stdout)
    assert html.index("The door opens") < html.index("Chloe") < html.index("&lt;hidden&gt;")
    assert html.index("&lt;hidden&gt;") < html.index("OOC · Sylvia")
    assert "<script>" not in html
    assert html.count('aria-label="NPC dialogue"') == 2
    assert "Old prose" in legacy


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "failed_after_accept", "new_draft"])
def test_composer_sends_one_typed_turn_and_clears_only_accepted_draft(outcome: str) -> None:
    script = r"""
const fs=require('fs'),assert=require('assert'),nodes=new Map();
global.window={};global.sessionStorage={removeItem(){}};
global.document={addEventListener(){},getElementById(id){
  if(!nodes.has(id))nodes.set(id,{value:'',open:false});return nodes.get(id);
}};
let source=fs.readFileSync(process.argv[1],'utf8');
source=source.replace('  // ---- Public API ----',`
  refreshSessionInputAdmission=()=>{};updateModelSwitchButtons=()=>{};
  updateCampaignSetupControl=()=>{};renderSessionModelState=()=>{};
  updateChronology=(container,html)=>{container.innerHTML=html;};
  startProjectionPolling=()=>{};stopProjectionPolling=()=>{};
  startStreamHeartbeat=()=>{};stopStreamHeartbeat=()=>{};
  renderBuilderChatRail=()=>{};renderHome=()=>{};renderPlayerProjection=renderDMSession;
  refreshCampaignIndex=async()=>{};setWebResearchPermission=()=>{};
  window.testState=state;window.saveDraft=saveSelectedSessionDraft;
  // ---- Public API ----`);
eval(source);
(async()=>{
  const outcome=process.argv[2],s=window.testState;
  s.view='session';s.activeSessionId='session-test';s.activeSessionRole='dm';
  s.activeCampaignId='campaign-test';s.campaigns=[{campaign_id:'campaign-test',turn_id:'turn-test'}];
  let accepted,sent,calls=0;
  const input=document.getElementById('player-input'),ooc=document.getElementById('ooc-input');
  const details=document.getElementById('ooc-note');
  input.value=' I attack the wolf ';ooc.value=' Keep Chloe affectionate. ';details.open=true;
  s.bridge={submit_session_input:async command=>{
    sent=command;calls++;
    if(outcome==='rejected')throw Error('synthetic admission rejection');
    accepted={message_id:command.message_id,kind:'user',content:{
      text:command.user_text,ooc_text:command.ooc_text}};
    if(outcome==='failed_after_accept')throw Error('synthetic provider failure');
    if(outcome==='new_draft'){input.value='My next message';window.saveDraft();}
    return {};
  },session_context:async()=>({messages:accepted?[accepted]:[]}),player_projection:async()=>[]};
  await window.SylviaGMFrontend.submitInput(input.value);
  assert.equal(calls,1);assert.equal(sent.user_text,' I attack the wolf ');
  assert.equal(sent.ooc_text,' Keep Chloe affectionate. ');
  if(outcome==='new_draft'){
    assert.equal(input.value,'My next message');
    assert.equal(s.sessionDrafts.get('session-test').text,'My next message');
  }else if(outcome==='rejected'){
    assert.equal(input.value,sent.user_text);assert.equal(ooc.value,sent.ooc_text);
    assert(details.open);assert(s.sessionDrafts.has('session-test'));
  }else{
    assert.equal(input.value,'');assert.equal(ooc.value,'');assert(!details.open);
    assert(!s.sessionDrafts.has('session-test'));
  }
})();
"""
    subprocess.run(
        ["node", "-e", script, str(FRONTEND_DIR / "app.js"), outcome],
        check=True, capture_output=True, text=True,
    )
