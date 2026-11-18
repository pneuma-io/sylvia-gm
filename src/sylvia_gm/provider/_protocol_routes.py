"""Documented protocol routes; discovery may override only the protocol identifier.

Sources checked 2026-10-14:
https://opencode.ai/v2/docs/console/models/
https://opencode.ai/v2/docs/console/go/
"""

from sylvia_gm.provider._model_facts import inferred_model_protocol

SUPPORTED_PROTOCOLS = frozenset(
    {
        "chat_completions",
        "responses",
        "anthropic_messages",
        "gemini_generate_content",
        "codex_app_server",
        "claude_code",
    }
)

def model_protocol(preset, model_id, declared=None, *, fallback="chat_completions"):
    """Use protocol metadata or documented routing, without forwarding upstream URLs."""
    if isinstance(declared, str) and declared in SUPPORTED_PROTOCOLS:
        return declared
    return inferred_model_protocol(preset, model_id) or fallback


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
