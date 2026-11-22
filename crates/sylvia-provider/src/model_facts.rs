//! `provider/_model_facts.py`: documented model facts keyed by service and exact model
//! identity. The tables are generated from the Python reference (`generate_tables.py`).

#[rustfmt::skip]
#[path = "model_facts_table.rs"]
mod table;

/// Whether a catalogue entry has a key, and its value (`"default_reasoning_effort": None` is
/// present).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Present<T> {
    Absent,
    Present(T),
}

/// One documented catalogue entry (`_DEFAULTS[...]`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Catalog {
    pub name: Option<&'static str>,
    pub context_window: Option<i64>,
    pub max_output_tokens: Option<i64>,
    pub transport: Option<&'static str>,
    pub supports_tools: Option<bool>,
    pub supports_streaming: Option<bool>,
    pub supported_reasoning_efforts: Option<&'static [&'static str]>,
    pub default_reasoning_effort: Present<Option<&'static str>>,
    pub documented_source: Option<&'static str>,
    pub documented_at: Option<&'static str>,
}

/// `ModelFacts`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ModelFacts {
    pub efforts: Option<&'static [&'static str]>,
    pub default_effort: &'static str,
    pub forced_tool_choice: bool,
    pub temperature: bool,
    pub binds_thinking: bool,
    /// Mid-conversation tool changes are accepted.
    pub tool_changes: bool,
    pub fast: bool,
    pub flex: bool,
    pub context_window: Option<i64>,
    pub gm_narration_hint: bool,
    /// `catalog`, empty in Python where this is `None`.
    pub catalog: Option<Catalog>,
}

/// `_UNKNOWN`.
pub const UNKNOWN: ModelFacts = ModelFacts {
    efforts: None,
    default_effort: "medium",
    forced_tool_choice: false,
    temperature: false,
    binds_thinking: false,
    tool_changes: false,
    fast: false,
    flex: false,
    context_window: None,
    gm_narration_hint: false,
    catalog: None,
};

/// `_MODEL_FACTS.get((route, model_id), _UNKNOWN)`.
pub fn facts(route: &str, model_id: &str) -> ModelFacts {
    table::FACTS.iter().find(|((r, m), _)| *r == route && *m == model_id).map_or(UNKNOWN, |(_, facts)| *facts)
}

/// `uses_gm_narration_hint(model)`: a ChatGPT-subscription route whose model the facts mark for
/// the GM narration reminder. The canonical id must be exactly `chatgpt-subscription/<wire id>`.
pub fn uses_gm_narration_hint(model: &crate::types::ModelInfo) -> bool {
    let wire_id = model.wire_model_id();
    model.id == format!("chatgpt-subscription/{wire_id}") && facts(&model.provider_key, wire_id).gm_narration_hint
}

/// `documented_openai_id(model_id)`.
pub fn documented_openai_id(model_id: &str) -> &str {
    table::OPENAI_ALIASES.iter().find(|(alias, _)| *alias == model_id).map_or(model_id, |(_, target)| target)
}

/// `openai_model_facts(model_id)`.
pub fn openai_model_facts(model_id: &str) -> ModelFacts {
    facts("openai", documented_openai_id(model_id))
}

/// `claude_model_id(model_id)`: the bare Claude identity, without a date suffix
/// (`re.sub(r"[-@]\d{8}$", "", ...)` over the case-folded last path segment).
pub fn claude_model_id(model_id: &str) -> Option<String> {
    let folded = sylvia_base::pystr::casefold(sylvia_base::pystr::strip(model_id));
    let last = folded.rsplit('/').next().unwrap_or_default();
    let chars: Vec<char> = last.chars().collect();
    // `$` also matches before a final newline.
    let end = if chars.last() == Some(&'\n') { chars.len() - 1 } else { chars.len() };
    let bare: String = if end >= 9
        && matches!(chars[end - 9], '-' | '@')
        && chars[end - 8..end].iter().all(|c| sylvia_base::pystr::decimal(*c).is_some())
    {
        chars[..end - 9].iter().chain(chars[end..].iter()).collect()
    } else {
        last.to_owned()
    };
    bare.starts_with("claude-").then_some(bare)
}

/// `claude_model_facts(model_id)`.
pub fn claude_model_facts(model_id: &str) -> ModelFacts {
    facts("anthropic", claude_model_id(model_id).as_deref().unwrap_or(""))
}

/// `claude_code_context_window(model_id)`.
pub fn claude_code_context_window(model_id: &str) -> Option<i64> {
    facts("claude_runtime", model_id).context_window
}

/// `documented_model_defaults(preset_id, model_id)`: the catalogue facts for an exact route and
/// identity.
pub fn documented_model_defaults(preset_id: &str, model_id: &str) -> Option<Catalog> {
    facts(preset_id, model_id).catalog
}

/// `documented_model_ids()`.
pub fn documented_model_ids() -> &'static [(&'static str, &'static str)] {
    table::DOCUMENTED_IDS
}

/// `inferred_model_protocol(preset, model_id)`: the protocol of a documented model family.
pub fn inferred_model_protocol(preset: &str, model_id: &str) -> Option<&'static str> {
    let identity = sylvia_base::pystr::casefold(model_id);
    for (route, exact, identities, protocol) in table::OPENCODE_ROUTES {
        if *route != preset {
            continue;
        }
        let matches = if *exact {
            identities.contains(&identity.as_str())
        } else {
            identities.iter().any(|prefix| identity.starts_with(prefix))
        };
        if matches {
            return Some(protocol);
        }
    }
    if preset == "openai"
        && ["gpt-5", "gpt-6", "o1", "o3", "o4"].iter().any(|prefix| identity.starts_with(prefix))
    {
        return Some("responses");
    }
    None
}
