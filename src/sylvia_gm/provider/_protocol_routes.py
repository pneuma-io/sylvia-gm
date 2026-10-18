"""Documented protocol routes; discovery may override only the protocol identifier.

Sources checked 2026-10-14:
https://opencode.ai/v2/docs/console/models/
https://opencode.ai/v2/docs/console/go/
"""

SUPPORTED_PROTOCOLS = frozenset(
    {
        "chat_completions",
        "responses",
        "anthropic_messages",
        "gemini_generate_content",
        "codex_app_server",
    }
)

_OPENCODE_ROUTES = {
    "opencode_zen": (
        ("exact", ("qwen3.8-max",), "chat_completions"),
        ("prefix", ("gemini-",), "gemini_generate_content"),
        ("prefix", ("gpt-5", "gpt-6", "grok-", "muse-spark-"), "responses"),
        ("prefix", ("claude-", "qwen3.5", "qwen3.6", "qwen3.7", "qwen3.8"), "anthropic_messages"),
    ),
    "opencode_go": (
        ("prefix", ("gemini-",), "gemini_generate_content"),
        ("prefix", ("gpt-5", "gpt-6", "grok-", "muse-spark-"), "responses"),
        (
            "prefix",
            ("claude-", "qwen3.5", "qwen3.6", "qwen3.7", "qwen3.8", "minimax-m"),
            "anthropic_messages",
        ),
    ),
}


def model_protocol(preset, model_id, declared=None, *, fallback="chat_completions"):
    """Use protocol metadata or documented routing, without forwarding upstream URLs."""
    if isinstance(declared, str) and declared in SUPPORTED_PROTOCOLS:
        return declared
    identity = model_id.casefold()
    for mode, identities, protocol in _OPENCODE_ROUTES.get(preset, ()):
        matches = identity in identities if mode == "exact" else identity.startswith(identities)
        if matches:
            return protocol
    if preset == "openai" and identity.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        return "responses"
    return fallback


def is_opencode_inference(base_url: str) -> bool:
    """Identify the documented gateway, without changing custom endpoint policy."""
    from urllib.parse import urlsplit

    url = urlsplit(base_url)
    return (
        url.scheme == "https"
        and url.netloc == "opencode.ai"
        and (
            url.path.startswith("/inference/") or url.path.rstrip("/") in {"/zen/v1", "/zen/go/v1"}
        )
    )


def inference_base_url(base_url: str, protocol: str) -> str:
    """Resolve saved OpenCode catalogue bases to the selected protocol endpoint."""
    from urllib.parse import urlsplit, urlunsplit

    if not is_opencode_inference(base_url):
        return base_url
    url = urlsplit(base_url)
    path = url.path.rstrip("/")
    path = {"/zen/v1": "/inference/v1", "/zen/go/v1": "/inference/go/v1"}.get(path, path)
    family = "/inference/go" if path.startswith("/inference/go/") else "/inference"
    suffixes = {
        "chat_completions": "/openai/v1",
        "responses": "/openai/v1",
        "anthropic_messages": "/anthropic/v1",
        "gemini_generate_content": "/google/v1beta",
    }
    known = {family + "/v1", *(family + suffix for suffix in suffixes.values())}
    if path not in known or protocol not in suffixes:
        return base_url
    return urlunsplit(
        (url.scheme, url.netloc, family + suffixes[protocol], url.query, url.fragment)
    )
