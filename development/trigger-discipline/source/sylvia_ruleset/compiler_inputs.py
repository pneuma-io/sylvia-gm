"""Retained compiled contracts for builds that have no access to the private vault."""

import json
from pathlib import Path


def public_compiler_inputs(value):
    """Keep compiled contracts while excluding raw note excerpts and canonical spans."""
    if isinstance(value, dict):
        return {key: public_compiler_inputs(child) for key, child in value.items()
                if key not in {"source_excerpt", "canonical_span"}}
    if isinstance(value, list):
        return [public_compiler_inputs(child) for child in value]
    return value


def retained_inputs() -> dict:
    """Load compiled mechanics and note identities, never vault text or canvases."""
    return json.loads(Path(__file__).with_name("compiled-inputs.json").read_bytes())
