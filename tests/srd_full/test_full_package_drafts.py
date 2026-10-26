"""Full's development closure uses current admission without changing Alpha defaults."""

import ast
import hashlib
import json
import zipfile
from pathlib import Path

from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.application.srd_full_packages import srd_full_drafts
from sylvia_gm.runtime._schema_validator import validate_ruleset

ROOT = Path(__file__).resolve().parents[2] / "rulesets" / "dnd-srd-5.2.1"
SOURCE_SHA256 = "8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87"


def test_full_drafts_bind_the_exact_reference_and_preserve_attribution():
    primary, reference = srd_full_drafts()
    assert primary.format_version == reference.format_version == 4
    assert primary.record["id"] == "wotc.srd.5.2.1"
    assert reference.record["id"] == "wotc.srd.5.2.1.reference"
    assert primary.record["dependencies"] == [
        {
            "namespace": "rules_reference",
            "kind": "rules_reference",
            "id": reference.record["id"],
            "version": reference.record["version"],
        }
    ]
    for draft in (primary, reference):
        assert draft.payloads["ATTRIBUTION.md"] == (
            ROOT / "ATTRIBUTION.md"
        ).read_bytes().replace(b"\r\n", b"\n")
    assert "runtime/ruleset.yaml" in primary.payloads
    assert "content/corpus/spells.json" in reference.payloads
    # Callers may edit drafts without mutating a later source read.
    original_record = dict(primary.record)
    primary.record["name"] = "Caller-local draft"
    assert srd_full_drafts()[0].record == original_record


def test_complete_reference_page_sequence_has_consistent_source_hashes():
    index = json.loads((ROOT / "content/corpus-index.json").read_text(encoding="utf-8"))
    pages = []
    for section in index["sections"]:
        data = (ROOT / section["artifact"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == section["artifact_sha256"]
        payload = json.loads(data)
        for page in payload["page_records"]:
            assert (
                hashlib.sha256(page["text"].encode()).hexdigest() == page["text_sha256"]
            )
            pages.append(page["page"])
    assert pages == list(range(1, 365))
    inventory = json.loads(
        (ROOT / "content/spell-status-inventory.json").read_text(encoding="utf-8")
    )
    assert inventory["source_sha256"] == SOURCE_SHA256
    assert (
        len({spell["id"] for spell in inventory["spells"]})
        == inventory["spell_count"]
        == 339
    )
    assert {spell["mechanics_status"] for spell in inventory["spells"]} <= {
        "implemented",
        "pending",
    }


def test_full_closure_is_deterministic_and_passes_current_generic_admission():
    drafts = srd_full_drafts()
    with materialize_package_archive(drafts) as first:
        first_bytes = first.archive.read()
        closure = first.closure_sha256
        with zipfile.ZipFile(first.archive) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["format"] == 4
            assert {item["id"] for item in manifest["packages"]} == {
                "wotc.srd.5.2.1",
                "wotc.srd.5.2.1.reference",
            }
    with materialize_package_archive(srd_full_drafts()) as second:
        assert second.archive.read() == first_bytes
        assert second.closure_sha256 == closure


def test_full_runtime_passes_current_generic_schema_validation():
    primary, _ = srd_full_drafts()
    assert validate_ruleset(primary.payloads["runtime/ruleset.yaml"].decode()) == []


def test_preserved_full_test_imports_have_local_targets():
    repository = Path(__file__).resolve().parents[2]
    missing = []
    for source in sorted(Path(__file__).parent.glob("*.py")):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "tests.srd_full":
                    modules.extend(f"{node.module}.{name.name}" for name in node.names)
                elif node.module.startswith("tests.srd_full."):
                    modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(
                    name.name
                    for name in node.names
                    if name.name.startswith("tests.srd_full.")
                )
            for module in modules:
                target = repository.joinpath(*module.split("."))
                if (
                    not target.with_suffix(".py").is_file()
                    and not (target / "__init__.py").is_file()
                ):
                    missing.append(f"{source.name}:{node.lineno}: {module}")
    assert missing == [], "Missing preserved Full dependencies:\n" + "\n".join(missing)
