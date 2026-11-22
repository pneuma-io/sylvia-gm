"""Write the model tables Rust copies from the locked Stainless SDKs (OpenAI's and Anthropic's).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/src/generate_sdk_models.py

The transports read responses the way the SDKs built them (`sdk_models.rs`): a declared field the
JSON lacks reads as `None`, an undeclared name falls back to `getattr`'s default, and a retained
native item is `model_dump(mode="json", exclude_none=True)` of its model (declared fields first,
in declaration order, then the extra fields the JSON carried; an integer in a `float` field
written as a float). That depends on the SDKs' model classes, so this writes, for every model
reachable from each table's roots, its declared fields in order, each with the shape of its type:

- `{"model": name}`, `{"list": shape}`, `{"dict": shape}`;
- `{"union": {type value: model, ...}, "first": model}`: a union of models told apart by their
  `type` field, with the variant the SDK falls back to for an unknown one;
- `{"either": [shape, ...]}`: another union, matched by the JSON value's own kind;
- `"float"`, or `"any"` for everything else.

Each field is `[name, JSON key (its alias), shape, default]`; the default is recorded only when it
is not `None`. The tables:

- `responses_models.json` (openai): `ResponseOutputItem`, `Response`, `ResponseStreamEvent`;
- `anthropic_models.json` (anthropic): `Message`, `RawMessageStreamEvent`, `ContentBlock`.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
import typing
from pathlib import Path

import anthropic
import openai
import pydantic
from anthropic.types import ContentBlock, Message, RawMessageStreamEvent
from openai.types.responses.response import Response
from openai.types.responses.response_output_item import ResponseOutputItem
from openai.types.responses.response_stream_event import ResponseStreamEvent

HERE = Path(__file__).parent


def unwrap(annotation):
    while typing.get_origin(annotation) is typing.Annotated:
        annotation = typing.get_args(annotation)[0]
    return annotation


def key(model) -> str:
    """A model's name in the table: its module's last part and its qualified name."""
    return f"{model.__module__.rsplit('.', 1)[-1]}.{model.__qualname__}"


def is_model(annotation) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel)


def type_literal(model) -> str | None:
    field = model.model_fields.get("type")
    if field is None:
        return None
    args = typing.get_args(unwrap(field.annotation))
    return args[0] if typing.get_origin(unwrap(field.annotation)) is typing.Literal and args else None


def shape(annotation, models: dict, queue: list):
    annotation = unwrap(annotation)
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)):
        variants = [unwrap(arg) for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(variants) == 1:
            return shape(variants[0], models, queue)
        if all(is_model(variant) for variant in variants) and all(type_literal(v) for v in variants):
            for variant in variants:
                queue.append(variant)
            return {"union": {type_literal(v): key(v) for v in variants}, "first": key(variants[0])}
        return {"either": [shape(variant, models, queue) for variant in variants]}
    if origin in (list, typing.List):
        return {"list": shape(typing.get_args(annotation)[0], models, queue)}
    if origin in (dict, typing.Dict):
        return {"dict": shape(typing.get_args(annotation)[1], models, queue)}
    if is_model(annotation):
        queue.append(annotation)
        return {"model": key(annotation)}
    if annotation is float:
        return "float"
    return "any"


def write(output: Path, package: str, version: str, roots: dict) -> None:
    models: dict = {}
    queue: list = []
    shapes = {name: shape(root, models, queue) for name, root in roots.items()}
    while queue:
        model = queue.pop(0)
        if key(model) in models:
            continue
        fields = []
        models[key(model)] = {"fields": fields}
        for name, field in model.model_fields.items():
            default = None if field.is_required() else field.default
            if default is not None:
                default = json.loads(json.dumps(default))
            # [field name, the JSON key it is read from, its shape, its default when not None]
            fields.append([name, field.alias or name, shape(field.annotation, models, queue), default])
    record = {package: version, "pydantic": pydantic.VERSION, "python": sys.version.split()[0],
              **shapes,
              "models": {name: entry["fields"] for name, entry in sorted(models.items())}}
    output.write_text(json.dumps(record, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    write(HERE / "responses_models.json", "openai", openai.__version__,
          {"output_item": ResponseOutputItem, "response": Response, "stream_event": ResponseStreamEvent})
    write(HERE / "anthropic_models.json", "anthropic", anthropic.__version__,
          {"message": Message, "stream_event": RawMessageStreamEvent, "content_block": ContentBlock})


if __name__ == "__main__":
    main()
