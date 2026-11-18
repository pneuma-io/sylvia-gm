"""Exact capabilities for OpenAI API model IDs and their documented aliases."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenAIModelFacts:
    efforts: tuple[str, ...] | None = None
    default_effort: str = "medium"
    fast: bool = False
    flex: bool = False


# https://developers.openai.com/api/docs/models
# https://developers.openai.com/api/docs/pricing
_EFFORT = ("none", "low", "medium", "high", "xhigh")
_EXTENDED_EFFORT = (*_EFFORT, "max")
_MODELS = {
    "gpt-4.1": OpenAIModelFacts(fast=True),
    "gpt-4.1-mini": OpenAIModelFacts(fast=True),
    "gpt-4o": OpenAIModelFacts(fast=True),
    "gpt-4o-mini": OpenAIModelFacts(fast=True),
    "gpt-5": OpenAIModelFacts(fast=True, flex=True),
    "gpt-5-mini": OpenAIModelFacts(fast=True, flex=True),
    "gpt-5-nano": OpenAIModelFacts(flex=True),
    "gpt-5.1": OpenAIModelFacts(fast=True, flex=True),
    "gpt-5.2": OpenAIModelFacts(efforts=_EFFORT, default_effort="none", fast=True, flex=True),
    "gpt-5.4": OpenAIModelFacts(fast=True, flex=True),
    "gpt-5.4-mini": OpenAIModelFacts(fast=True, flex=True),
    "gpt-5.4-nano": OpenAIModelFacts(flex=True),
    "gpt-5.4-pro": OpenAIModelFacts(flex=True),
    "gpt-5.5": OpenAIModelFacts(efforts=_EFFORT, fast=True, flex=True),
    "gpt-5.5-pro": OpenAIModelFacts(flex=True),
    "gpt-5.6-luna": OpenAIModelFacts(fast=True, flex=True),
    "gpt-5.6-sol": OpenAIModelFacts(efforts=_EXTENDED_EFFORT, fast=True, flex=True),
    "gpt-5.6-terra": OpenAIModelFacts(fast=True, flex=True),
    "gpt-6-astra": OpenAIModelFacts(fast=True, flex=True),
    "gpt-6-luna": OpenAIModelFacts(fast=True, flex=True),
    "gpt-6-sol": OpenAIModelFacts(fast=True, flex=True),
    "gpt-6.1-sol": OpenAIModelFacts(fast=True, flex=True),
    "gpt-6.1-terra": OpenAIModelFacts(fast=True, flex=True),

}
_ALIASES = {"gpt-5.6": "gpt-5.6-sol", "gpt-5.5-2026-04-23": "gpt-5.5"}
_UNKNOWN = OpenAIModelFacts()


def documented_openai_id(model_id: str) -> str:
    return _ALIASES.get(model_id, model_id)


def openai_model_facts(model_id: str) -> OpenAIModelFacts:
    return _MODELS.get(documented_openai_id(model_id), _UNKNOWN)
