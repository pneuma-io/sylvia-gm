"""Apply the bounded Lite GM feedback repair and regenerate ordinary package claims."""

import argparse
import json
import re
import textwrap
import zipfile
from pathlib import Path

import yaml

from sylvia_gm.application.bundled_packages import _srd_source_drafts
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations

from .build_srd_5_2_1_mechanics import _tool_schema
from .srd_lite_gm_feedback import CAST_ROOTS, bind_gm_feedback, retire_refusal_stubs
from .srd_timed_actor_defenses import _RuntimeDumper


def _replace_entry(text, section, name, value):
    """Rewrite one named mapping entry, preserving unrelated authored YAML bytes."""
    start = text.index(section + ":\n") + len(section) + 2
    end = re.search(r"(?m)^[A-Za-z_][A-Za-z0-9_]*:", text[start:])
    end = start + end.start() if end else len(text)
    body = text[start:end]
    pattern = re.compile(
        r"(?ms)^  " + re.escape(name) + r":[^\n]*\n.*?(?=^  [A-Za-z_][A-Za-z0-9_.-]*:|\Z)"
    )
    rendered = (
        ""
        if value is None
        else textwrap.indent(
            yaml.dump(
                {name: value},
                Dumper=_RuntimeDumper,
                sort_keys=False,
                allow_unicode=True,
                width=98,
            ),
            "  ",
        )
    )
    if pattern.search(body):
        body = pattern.sub(lambda _match: rendered, body, count=1)
    else:
        body += rendered
    return text[:start] + body + text[end:]


def update(version):
    root = Path(__file__).resolve().parents[1]
    package = root / "rulesets/srd-lite"
    path = package / "runtime/ruleset.yaml"
    original = path.read_text(encoding="utf-8")
    runtime = yaml.load(original, Loader=yaml.CSafeLoader)
    record_path = package / "package-record.json"
    record = json.loads(record_path.read_bytes())
    before = {name: json.dumps(tool, sort_keys=True) for name, tool in runtime["tools"].items()}
    retired, removed = retire_refusal_stubs(runtime, record)
    aliases = bind_gm_feedback(runtime)
    result = original
    changed = []
    for name in sorted(removed):
        result = _replace_entry(result, "tools", name, None)
    for name, tool in runtime["tools"].items():
        if json.dumps(tool, sort_keys=True) != before[name]:
            result = _replace_entry(result, "tools", name, tool)
            changed.append(name)
    for name in ("srd_lite_single_target_spells", "srd_lite_multiple_target_spells"):
        result = _replace_entry(result, "definitions", name, runtime["definitions"][name])
    # Verify the edited fragments still describe exactly the transformed runtime.
    if yaml.load(result, Loader=yaml.CSafeLoader) != runtime:
        raise ValueError("Targeted runtime rendering changed unrelated data")
    path.write_bytes(result.encode("utf-8"))

    expanded = resolve_parameter_declarations(runtime)
    for tool in record["tools"]:
        if tool["name"] in changed:
            tool["input_schema"] = _tool_schema(
                expanded["tools"][tool["name"]],
                tool["input_schema"],
            )
        if tool["name"] in CAST_ROOTS:
            tool["input_schema"]["properties"]["spell"]["x-sylvia-gm-value-aliases"] = aliases
            tool["input_schema"]["properties"]["spell"]["x-sylvia-gm-castable-spells"] = True
    record["version"] = version
    record_path.write_bytes((json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    identity_path = package / "package-identity.json"
    identity = json.loads(identity_path.read_bytes())
    identity["primary"]["version"] = version
    identity_path.write_bytes(
        (json.dumps(identity, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    declarations_path = package / "content/content-declarations.json"
    declarations = json.loads(declarations_path.read_bytes())
    for inclusion in declarations["inclusions"]:
        links = inclusion.get("links", {})
        if "tools" in links:
            links["tools"] = [name for name in links["tools"] if name not in retired]
    declarations_path.write_bytes(canonical_json_bytes(declarations))
    with materialize_package_archive(_srd_source_drafts()) as artifact:
        with zipfile.ZipFile(artifact.archive) as archive:
            manifests = json.loads(archive.read("manifest.json"))["packages"]
        primary = next(p for p in manifests if p["id"] == record["id"])
        record_path.write_bytes(
            (json.dumps(primary, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        print(
            json.dumps(
                {
                    "version": version,
                    "content_sha256": primary["content_sha256"],
                    "closure_sha256": artifact.closure_sha256,
                    "changed_tools": changed,
                }
            ),
            flush=True,
        )
    catalogue = root / "scripts/srd_lite_catalogue.py"
    text = catalogue.read_text(encoding="utf-8")
    text = re.sub(r'^VARIANT_VERSION = "[^"]+"', f'VARIANT_VERSION = "{version}"', text, flags=re.M)
    catalogue.write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    update(parser.parse_args().version)
