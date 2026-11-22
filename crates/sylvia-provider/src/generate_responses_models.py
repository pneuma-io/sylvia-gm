"""Write the Responses output-item model table Rust copies from the locked OpenAI SDK.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/src/generate_responses_models.py

`native_output` keeps each Responses output item as `item.model_dump(mode="json",
exclude_none=True)` of the model the SDK constructed from the response JSON. That dump depends on
the SDK's model classes, and so do the attributes the transport reads (a declared field the JSON
lacks reads as `None`; an undeclared one falls back to `getattr`'s default): declared fields first, in declaration order, then the extra fields the
JSON carried; nested values dumped by their declared types; an integer in a `float` field
written as a float. This writes `responses_models.json`: for every model reachable from
`ResponseOutputItem`, `Response` and `ResponseStreamEvent` (the three roots), its declared fields in order, each with the shape of its declared type:

- `{"model": name}`, `{"list": shape}`, `{"dict": shape}`;
- `{"union": {type value: model, ...}, "first": model}`: a union of models told apart by their
  `type` field, with the variant the SDK falls back to for an unknown one;
- `{"either": [shape, ...]}`: another union, matched by the JSON value's own kind;
- `"float"`, or `"any"` for everything else.

Each field is `[name, JSON key (its alias), shape, default]`; the default is recorded only when it
is not `None` (an attribute the JSON lacks reads as it). Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
import typing
from pathlib import Path

import openai
import pydantic
from openai.types.responses.response import Response
from openai.types.responses.response_output_item import ResponseOutputItem
from openai.types.responses.response_stream_event import ResponseStreamEvent

OUTPUT = Path(__file__).with_name("responses_models.json")


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


def main() -> None:
    models: dict = {}
    queue: list = []
    roots = {"output_item": shape(ResponseOutputItem, models, queue), "response": shape(Response, models, queue),
             "stream_event": shape(ResponseStreamEvent, models, queue)}
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
    record = {"openai": openai.__version__, "pydantic": pydantic.VERSION, "python": sys.version.split()[0],
              **roots,
              "models": {name: entry["fields"] for name, entry in sorted(models.items())}}
    OUTPUT.write_text(json.dumps(record, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
