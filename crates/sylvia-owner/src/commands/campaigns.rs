//! Campaign commands: `interfaces/commands/campaigns.py`.

use crate::dispatch::DispatchContext;
use sylvia_base::{CodedResult, Json, JsonMap};

/// `handle_handshake_status`: `ctx.bus.handshake_status()`, the supervisor's view of the
/// launch handshake (`LocalCommandBus.handshake_status` in `interfaces/local_api.py`).
pub async fn handle_handshake_status(ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
    let verified = ctx.handshake_verified;
    let mut status = JsonMap::new();
    status.insert("verified".into(), verified.into());
    status.insert("role".into(), if verified { "owner" } else { "pending" }.into());
    status.insert(
        "reason".into(),
        if verified { "launch handshake verified" } else { "handshake pending" }.into(),
    );
    Ok(Json::Object(status))
}
