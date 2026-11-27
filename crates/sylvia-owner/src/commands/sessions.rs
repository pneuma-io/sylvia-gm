//! Session commands: `interfaces/commands/sessions.py`. The commands that run the session loop
//! (`submit_session_input`, `start_dm_session`, `manual_retry_session`, `stop_agent_session`,
//! `steer_session`) and the narration stream commands come in later PRs, on lane 4's
//! `CampaignSessionLoop` and lane 3's narration attempts.

use crate::dispatch::DispatchContext;
use sylvia_base::{CodedResult, Json, JsonMap};

/// `handle_resume_agent_session`: `ctx.bus.resume_agent_session(session_id)`, through the
/// supervisor's verified owner (`LocalOwnerSupervisor.resume_agent_session`) to
/// `AuthorityOwner.resume_agent_session`: one explicitly selected stopped writer session is
/// active again.
pub async fn handle_resume_agent_session(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let session_id = super::campaigns::required(params, "session_id")?;
    let record = ctx
        .store
        .call(move |store| store.resume_agent_session(&session_id))
        .await
        .map_err(super::campaigns::store_error)?;
    Ok(super::builder::session_record_value(&record))
}
