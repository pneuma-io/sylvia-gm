//! `agent/_inference_identity.py`: a provider request context identity.

use serde_json::json;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_provider::types::ModelInfo;

/// `inference_context_id(owner, model)`: excludes request revisions and user-facing names, and
/// changes on compatibility changes. `json.dumps(identity, sort_keys=True,
/// separators=(",", ":"))`, ASCII-escaped.
pub fn inference_context_id(owner: &JsonMap, model: Option<&ModelInfo>) -> String {
    let field = |value: Option<&str>| value.map_or(Json::Null, Json::from);
    let identity = json!({
        "owner": owner,
        "provider": field(model.map(|model| model.provider_key.as_str())),
        "model": field(model.map(|model| model.id.as_str())),
        "transport": field(model.map(|model| model.transport.as_str())),
        "wire_model": field(model.map(ModelInfo::wire_model_id)),
    });
    let encoded = pyjson::dumps(&identity, Dump::CANONICAL.ascii(true));
    format!("sylvia-{}", sha256_hex(encoded.as_bytes()))
}
