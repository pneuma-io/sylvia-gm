//! `provider/_models.py`: the built-in model catalogue (18 entries), and
//! `provider/_output_policy.py`: Sylvia's reply allowance for a model.

use crate::types::{transport, ModelInfo};

/// `(id, name, provider_name, provider_key, provider_mode, recommended, premium, context_window,
/// max_output_tokens, cost_per_1k_tokens, transport)`, in Python's order.
type Entry = (
    &'static str,
    &'static str,
    &'static str,
    &'static str,
    &'static str,
    bool,
    bool,
    i64,
    i64,
    f64,
    &'static str,
);

const CHAT: &str = transport::CHAT_COMPLETIONS;
const MESSAGES: &str = transport::ANTHROPIC_MESSAGES;

#[rustfmt::skip]
const ENTRIES: [Entry; 18] = [
    ("opencode-go/deepseek-v4", "DeepSeek V4", "OpenCode Go", "opencode_go", "byok", true, false, 1_048_576, 4096, 0.00055, CHAT),
    ("opencode-go/deepseek-v3", "DeepSeek V3", "OpenCode Go", "opencode_go", "byok", false, false, 131_072, 4096, 0.00027, CHAT),
    ("opencode-go/deepseek-r1", "DeepSeek R1", "OpenCode Go", "opencode_go", "byok", false, false, 131_072, 4096, 0.00055, CHAT),
    ("opencode-go/claude-opus-4-5", "Claude Opus 4.5", "OpenCode Go", "opencode_go", "byok", false, true, 200_000, 4096, 0.015, CHAT),
    ("opencode-go/claude-sonnet-4-5", "Claude Sonnet 4.5", "OpenCode Go", "opencode_go", "byok", false, true, 200_000, 4096, 0.003, CHAT),
    ("opencode-go/gpt-5", "GPT-5", "OpenCode Go", "opencode_go", "byok", false, true, 128_000, 4096, 0.015, CHAT),
    ("openrouter/deepseek-v4", "DeepSeek V4", "OpenRouter", "openrouter", "byok", false, false, 1_048_576, 4096, 0.00055, CHAT),
    ("openrouter/deepseek-r1", "DeepSeek R1", "OpenRouter", "openrouter", "byok", false, false, 131_072, 4096, 0.00055, CHAT),
    ("openrouter/claude-sonnet-4-5", "Claude Sonnet 4.5", "OpenRouter", "openrouter", "byok", false, true, 200_000, 4096, 0.003, CHAT),
    ("openrouter/gemini-2.5-pro", "Gemini 2.5 Pro", "OpenRouter", "openrouter", "byok", false, true, 1_048_576, 4096, 0.00125, CHAT),
    ("ollama/qwen2.5:14b", "Qwen 2.5 14B", "Ollama", "ollama", "ollama", false, false, 32_768, 4096, 0.0, CHAT),
    ("ollama/qwen2.5:7b", "Qwen 2.5 7B", "Ollama", "ollama", "ollama", false, false, 32_768, 4096, 0.0, CHAT),
    ("ollama/llama3.1:8b", "Llama 3.1 8B", "Ollama", "ollama", "ollama", true, false, 131_072, 4096, 0.0, CHAT),
    ("ollama/llama3.1:70b", "Llama 3.1 70B", "Ollama", "ollama", "ollama", false, false, 131_072, 4096, 0.0, CHAT),
    ("ollama/mistral:7b", "Mistral 7B", "Ollama", "ollama", "ollama", false, false, 32_768, 4096, 0.0, CHAT),
    ("ollama/phi3:mini", "Phi-3 Mini", "Ollama", "ollama", "ollama", false, false, 4096, 1024, 0.0, CHAT),
    ("anthropic/claude-sonnet-4-5", "Claude Sonnet 4.5", "Anthropic", "anthropic", "byok", false, true, 200_000, 4096, 0.003, MESSAGES),
    ("anthropic/claude-haiku-3-5", "Claude Haiku 3.5", "Anthropic", "anthropic", "byok", false, false, 200_000, 4096, 0.00025, MESSAGES),
];

/// `build_catalog()`: every entry's catalogue window is its declared window; Ollama's effective
/// window stays unknown until the runtime reports one.
pub fn build_catalog() -> Vec<ModelInfo> {
    ENTRIES
        .iter()
        .map(|&(id, name, provider_name, key, mode, recommended, premium, window, output, cost, wire)| {
            let mut model = ModelInfo::new(id, name, provider_name, key, mode);
            model.recommended = recommended;
            model.premium = premium;
            model.context_window = Some(window);
            model.max_output_tokens = Some(output);
            model.cost_per_1k_tokens = cost;
            model.transport = wire.into();
            // The `ModelInfo(...)` call, then `replace(...)`: both run `__post_init__`.
            let mut model = model.post_init();
            let ollama = model.provider_mode == "ollama";
            model.declared_context_window = model.context_window;
            model.declared_context_source = "model_catalog".into();
            model.context_window = if ollama { None } else { model.context_window };
            model.context_window_source = if ollama { "unknown" } else { "model_catalog" }.into();
            model.post_init()
        })
        .collect()
}

/// `DEFAULT_OUTPUT_ALLOWANCE`.
pub const DEFAULT_OUTPUT_ALLOWANCE: i64 = 4096;

/// `output_allowance(model, builder=...)`: an enforceable allowance, or `None` for
/// runtime-managed output.
pub fn output_allowance(model: &ModelInfo, builder: bool) -> Option<i64> {
    if matches!(model.transport.as_str(), transport::CODEX_APP_SERVER | transport::CLAUDE_CODE) {
        return None;
    }
    let value = model.max_output_tokens;
    let mut allowance = value.filter(|&value| value > 0).unwrap_or(DEFAULT_OUTPUT_ALLOWANCE);
    if value.is_none() {
        if let Some(context) = model.context_window.filter(|&context| context > 0) {
            allowance = allowance.min((context.div_euclid(4)).max(1));
        }
    }
    if builder && model.builder_output_tokens > 0 {
        allowance = allowance.min(model.builder_output_tokens);
    }
    Some(allowance)
}
