"""Generate reference vectors for sylvia_app::reusable_plans (application/reusable_plans.py).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_reusable_plan_vectors.py

`steps` is one scenario in a fresh storage root, which the Rust test replays in its own:
- saves: Rift siblings (`save_reusable_rift_plan`) of the bundled Rift Plan, with and without
  its retained snapshot, over title and rendering variants (including `re.sub` template escapes
  in titles), and `save_reusable_campaign_plan` with each validator, setup and contribution;
- loads with each validator, catalogs with and without an invalid-entry handler, deletions;
- tampering, each undone before the next: record edits, re-encoded or damaged files, missing
  and extra files, an oversized snapshot, a linked entry (a junction on Windows, a symbolic
  link elsewhere) and an oversized library.
`texts` holds `_strip_section` and the retitling `re.sub` on random renderings.
Machine paths are masked as <ROOT>. Outcomes are a result shape or [class, message].
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application import bundled_packages as bp  # noqa: E402
from sylvia_gm.application import reusable_plans as rp  # noqa: E402
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot  # noqa: E402
from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.domain.campaign_plan import validate_campaign_plan  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402

OUTPUT = Path(__file__).with_name("reusable_plan_vectors.json")
RETAINED_PATH = "campaign-plans/the-rift-awakening/setting.sylvia-gm-package"
CONTRIBUTION_PATH = "campaign-plans/riichi-quick-test/interview-contribution.json"
rng = random.Random(20261124)
B, NL = chr(92), chr(10)
LINKED_ID = "campaign-plan-reusable-" + "0" * 24 + "-" + "0" * 16
SECTIONS = ["Selected campaign party", "Player customization brief", "Accepted Campaign Construction"]


def digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


class Scenario:
    def __init__(self, storage: Path):
        self.storage = storage
        self.retained = (ROOT / RETAINED_PATH).read_bytes()
        self.rift = bp.bundled_rift_campaign_plan()
        authority = bp.bundled_rift_authority()
        self.validators = {
            "default": None,
            "rift": rp._validate_rift_plan,
            "snapshot": lambda value, text: validate_plan_snapshot(self.retained, value, text),
            "current": lambda value, text: validate_campaign_plan(
                value, readable_markdown=text, expected_ruleset=authority.ruleset,
                expected_setting=authority.setting, expected_closure_sha256=authority.closure_sha256,
                setting_paths=authority.setting_paths, setting_knowledge_ids=authority.setting_knowledge_ids),
        }
        valid = json.loads((ROOT / CONTRIBUTION_PATH).read_text(encoding="utf-8"))
        self.contributions = {"none": None, "invalid": {"format_id": "x"}, "valid": valid}
        self.backups = {}

    def mask(self, text: str) -> str:
        for root in (str(self.storage), str(self.storage.resolve())):
            for form in (root.replace(B, B + B), root, root.replace(B, "/")):
                text = text.replace(form, "<ROOT>")
        # Separators inside a masked path become "/"; other backslashes are kept.
        parts = text.split("<ROOT>")
        for index in range(1, len(parts)):
            path, quote, rest = parts[index].partition("'")
            parts[index] = path.replace(B + B, "/").replace(B, "/") + quote + rest
        return "<ROOT>".join(parts)

    def entry(self, artifact_id: str) -> Path:
        return self.storage / "campaign-plans" / artifact_id

    def markdown(self, variant: str) -> str:
        text = self.rift.readable_markdown
        if variant == "rift":
            return text
        if variant == "sections":
            return (text + NL + NL + "## Selected campaign party" + NL + "Alice" + NL + NL + "## Player customization brief  "
                    + NL + NL + "Be kind." + NL + "## Accepted Campaign Construction" + NL)
        if variant == "crlf":
            return text.replace(NL, chr(13) + NL)
        if variant == "untitled":
            return text.split(NL, 1)[1]
        if variant == "indented":
            return "  # Indented" + NL + text
        raise AssertionError(variant)

    def plan_shape(self, plan, files: bool):
        shape = {"artifact_id": plan.artifact_id, "title": plan.title, "summary": plan.summary,
                 "content_sha256": plan.content_sha256, "plan_content_sha256": plan.plan.content_sha256,
                 "plan_artifact_id": plan.plan.artifact_id,
                 "markdown_sha256": digest(plan.plan.readable_markdown.encode("utf-8")),
                 "interview_contribution": thaw_json_value(plan.interview_contribution),
                 "setup_id": plan.setup_id, "snapshot_sha256": digest(plan.package_snapshot),
                 "catalog": thaw_json_value(plan.catalog_payload())}
        if files:
            entry = self.entry(plan.artifact_id)
            shape["files"] = {item.name: digest(item.read_bytes()) for item in sorted(entry.iterdir())}
            shape["storage"] = sorted(item.name for item in self.storage.iterdir())
        return shape

    def outcome(self, call):
        try:
            return ["ok", call()]
        except Exception as error:  # every class is behaviour
            return [type(error).__name__, self.mask(str(error))]

    def run(self, step: dict):
        op = step["op"]
        if op == "save_rift":
            return self.outcome(lambda: self.plan_shape(rp.save_reusable_rift_plan(
                self.storage, operation_id=step["operation_id"], title=step["title"],
                source_payload=self.rift.payload, source_markdown=self.markdown(step["markdown"]),
                package_snapshot=self.retained if step["snapshot"] else None), True))
        if op == "save_plan":
            return self.outcome(lambda: self.plan_shape(rp.save_reusable_campaign_plan(
                self.storage, operation_id=step["operation_id"], source_payload=self.rift.payload,
                source_markdown=self.markdown(step["markdown"]), validate_plan=self.validators[step["validator"]],
                package_snapshot=self.retained if step["snapshot"] else None, setup_id=step["setup_id"],
                interview_contribution=self.contributions[step["contribution"]]), True))
        if op == "load":
            return self.outcome(lambda: self.plan_shape(rp.load_reusable_campaign_plan(
                self.storage, step["artifact"], validate_plan=self.validators[step["validator"]]), False))
        if op == "catalog":
            invalid = []
            handler = ((lambda name, error: invalid.append([name, type(error).__name__, self.mask(str(error))]))
                       if step["handler"] else None)
            result = self.outcome(lambda: [self.plan_shape(plan, False) for plan in rp.reusable_campaign_plan_catalog(
                self.storage, validate_plan=self.validators[step["validator"]], on_invalid=handler)])
            return [*result, invalid] if result[0] == "ok" else result
        if op == "delete":
            return self.outcome(lambda: rp.delete_reusable_campaign_plan(
                self.storage, step["artifact"], expected_content_sha256=step["hash"],
                confirmation=step["confirmation"]))
        return ["ok", self.tamper(step)]

    def tamper(self, step: dict):
        op, entry = step["op"], self.entry(step.get("artifact", ""))
        if op == "backup":
            target = self.storage.parent / ("backup-" + step["label"])
            shutil.copytree(entry, target)
            self.backups[step["label"]] = target
        elif op == "restore":
            shutil.rmtree(entry)
            shutil.copytree(self.backups[step["label"]], entry)
        elif op == "edit_record":
            record = json.loads((entry / "record.json").read_text(encoding="utf-8"))
            for kind, key, value in step["edits"]:
                if kind == "delete":
                    record.pop(key, None)
                else:
                    record[key] = value
            (entry / "record.json").write_bytes(canonical_json_bytes(record))
        elif op == "write":
            (entry / step["name"]).write_bytes(bytes.fromhex(step["hex"]))
        elif op == "transform":
            path = entry / step["name"]
            data = path.read_bytes()
            text = data.decode("utf-8")
            data = {"bom": lambda: b"\xef\xbb\xbf" + data, "double_bom": lambda: b"\xef\xbb\xbf\xef\xbb\xbf" + data,
                    "utf-16": lambda: text.encode("utf-16-le") and b"\xff\xfe" + text.encode("utf-16-le"),
                    "utf-16-le": lambda: text.encode("utf-16-le"), "utf-32-be": lambda: text.encode("utf-32-be"),
                    "truncate": lambda: data[:-1], "garbage": lambda: data + b"\xff",
                    "crlf": lambda: data.replace(b"\n", b"\r\n") + b"\r\n"}[step["transform"]]()
            path.write_bytes(data)
        elif op == "remove":
            (entry / step["name"]).unlink()
        elif op == "mkdir":
            (entry / step["name"]).mkdir()
        elif op == "big_snapshot":
            (entry / "setting.sylvia-gm-package").write_bytes(bytes(64 * 1024 * 1024 + 1))
        elif op == "link":
            link = self.entry(LINKED_ID)
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(entry), str(link))
            else:
                os.symlink(entry, link, target_is_directory=True)
        elif op == "unlink":
            link = self.entry(LINKED_ID)
            os.rmdir(link) if os.name == "nt" else os.unlink(link)
        elif op == "many_dirs":
            for index in range(step["count"]):
                (self.storage / "campaign-plans" / f"filler-{index:03d}").mkdir()
        elif op == "clear_dirs":
            for path in (self.storage / "campaign-plans").glob("filler-*"):
                path.rmdir()
        else:
            raise AssertionError(op)
        return None


TITLES = ["My Rift", " padded", "padded ", "", "x" * 160, "x" * 161, "nul" + chr(0) + "title", "Two" + NL + "lines",
          "a" + B + "nb", "C:" + B + "Temp", B + "g<0> copy", "Group " + B + "1", B + "g<name>", B + "g<1x>",
          "end" + B, B + "0" + B + "012" + B + "101", B + "400", B + "-dash", B + "99", "line" + NL + "two " + B + "q",
          "é " + B + "g<٣>", "Rift " + B + B + " slash"]


def scenario_steps(scenario: Scenario) -> list:
    steps = []

    def step(**fields):
        fields["outcome"] = scenario.run(fields)
        steps.append(fields)
        return fields["outcome"]

    def save_id(outcome):
        assert outcome[0] == "ok", outcome
        return outcome[1]["artifact_id"]

    a = save_id(step(op="save_rift", operation_id="op-a", title="My Rift", markdown="rift", snapshot=False))
    b = save_id(step(op="save_rift", operation_id="op-b", title="Snapshot Rift", markdown="sections", snapshot=True))
    step(op="save_rift", operation_id="op-a", title="My Rift", markdown="rift", snapshot=False)
    for index, title in enumerate(TITLES):
        step(op="save_rift", operation_id=f"title-{index}", title=title, markdown="rift", snapshot=False)
    for variant in ["sections", "crlf", "untitled", "indented"]:
        step(op="save_rift", operation_id=f"markdown-{variant}", title="Variant", markdown=variant, snapshot=False)
    c = save_id(step(op="save_plan", operation_id="plan-c", markdown="rift", validator="snapshot", snapshot=True,
                     setup_id=None, contribution="none"))
    d = save_id(step(op="save_plan", operation_id="plan-d", markdown="rift", validator="snapshot", snapshot=False,
                     setup_id="the-rift-awakening", contribution="valid"))
    step(op="save_plan", operation_id="plan-e", markdown="rift", validator="snapshot", snapshot=True,
         setup_id="other", contribution="none")
    for validator, setup_id, contribution in [("snapshot", None, "invalid"), ("current", None, "none"),
                                              ("rift", "", "none"), ("rift", None, "none")]:
        step(op="save_plan", operation_id=f"plan-{validator}-{contribution}", markdown="rift", validator=validator,
             snapshot=False, setup_id=setup_id, contribution=contribution)
    for artifact in [a, b, c, d, "not-an-id", LINKED_ID]:
        for validator in ["default", "rift", "snapshot", "current"]:
            step(op="load", artifact=artifact, validator=validator)
    step(op="catalog", validator="default", handler=True)
    step(op="catalog", validator="default", handler=False)
    step(op="catalog", validator="snapshot", handler=True)

    def tampered(artifact, *changes, validators=("default",)):
        step(op="backup", artifact=artifact, label=f"t{len(steps)}")
        label = steps[-1]["label"]
        for change in changes:
            step(artifact=artifact, **change)
        for validator in validators:
            step(op="load", artifact=artifact, validator=validator)
        step(op="restore", artifact=artifact, label=label)

    other_hash = "a" * 64
    record_edits = [[["set", "format_version", 1]], [["set", "format_version", True]], [["set", "format_version", 1.0]],
                    [["set", "format_version", "3"]], [["set", "format_version", 4]], [["set", "format_version", None]],
                    [["delete", "format_version", None]], [["set", "extra", 1]], [["set", "artifact_id", LINKED_ID]],
                    [["set", "format_id", "other"]], [["set", "setup_id", ""]], [["set", "setup_id", 5]],
                    [["set", "setup_id", "x" + chr(0)]], [["set", "package_snapshot", "other"]],
                    [["set", "package_snapshot_sha256", "ABC"]], [["set", "package_snapshot_sha256", other_hash]],
                    [["set", "title", "Other"]], [["set", "summary", "Other"]], [["set", "plan_content_sha256", other_hash]],
                    [["set", "content_sha256", other_hash]], [["delete", "summary", None]],
                    [["delete", "setup_id", None], ["set", "format_version", 1]],
                    [["delete", "setup_id", None], ["delete", "package_snapshot", None],
                     ["delete", "package_snapshot_sha256", None], ["set", "format_version", 1]]]
    for artifact in [a, b]:
        for edits in record_edits:
            tampered(artifact, {"op": "edit_record", "edits": edits})
        for transform in ["bom", "double_bom", "utf-16", "utf-16-le", "utf-32-be", "truncate", "garbage", "crlf"]:
            tampered(artifact, {"op": "transform", "name": "plan.json", "transform": transform})
        for transform in ["bom", "garbage", "crlf"]:
            tampered(artifact, {"op": "transform", "name": "plan.md", "transform": transform})
            tampered(artifact, {"op": "transform", "name": "record.json", "transform": transform})
        tampered(artifact, {"op": "remove", "name": "plan.md"})
        tampered(artifact, {"op": "write", "name": "notes.txt", "hex": "00"})
        tampered(artifact, {"op": "mkdir", "name": "sub"})
        tampered(artifact, {"op": "write", "name": "interview-contribution.json", "hex": b'{"bad": 1}'.hex()})
    tampered(b, {"op": "remove", "name": "setting.sylvia-gm-package"})
    tampered(b, {"op": "big_snapshot"})
    tampered(b, {"op": "write", "name": "setting.sylvia-gm-package", "hex": "00"})
    tampered(d, {"op": "remove", "name": "interview-contribution.json"}, validators=("default", "snapshot"))
    tampered(a, {"op": "write", "name": "setting.sylvia-gm-package", "hex": "00"})
    step(op="link", artifact=b)
    step(op="load", artifact=LINKED_ID, validator="default")
    step(op="catalog", validator="default", handler=True)
    step(op="delete", artifact=LINKED_ID, hash=other_hash, confirmation=f"permanently delete {LINKED_ID}")
    step(op="unlink")
    step(op="many_dirs", count=256 - 40)
    step(op="catalog", validator="default", handler=True)
    step(op="many_dirs", count=0)
    step(op="clear_dirs")
    record_b = json.loads((scenario.entry(b) / "record.json").read_text(encoding="utf-8"))
    exact = f"permanently delete {b}"
    for artifact, digest_value, confirmation in [("bad", other_hash, exact), (b, "XYZ", exact), (b, other_hash, "no"),
                                                 (b, other_hash, exact), (b, record_b["content_sha256"], exact),
                                                 (b, record_b["content_sha256"], exact), (LINKED_ID, other_hash,
                                                                                          f"permanently delete {LINKED_ID}")]:
        step(op="delete", artifact=artifact, hash=digest_value, confirmation=confirmation)
    step(op="load", artifact=b, validator="default")
    step(op="catalog", validator="default", handler=True)
    return steps


def text_cases() -> list:
    fragments = [NL, NL + NL, "## Selected campaign party", "## Selected campaign party extra", "## Accepted Campaign Construction",
                 " ", chr(9), chr(11), chr(28), "text", "## Other", NL + "## ", "#", chr(13), "# ", "# Title", "  "]
    cases = []
    for _ in range(500):
        markdown = "".join(rng.choice(fragments) for _ in range(rng.randrange(0, 10)))
        heading = rng.choice(SECTIONS)
        title = rng.choice(TITLES[:4] + TITLES[8:])
        try:
            retitled = ["ok", re.sub(r"^# .*?$", f"# {title}", markdown, count=1, flags=re.MULTILINE)]
        except Exception as error:  # every class is behaviour
            retitled = [type(error).__name__, str(error)]
        cases.append({"markdown": markdown, "heading": heading, "stripped": rp._strip_section(markdown, heading),
                      "title": title, "retitled": retitled})
    return cases


def main() -> None:
    with tempfile.TemporaryDirectory() as scratch:
        storage = Path(scratch) / "storage"
        storage.mkdir()
        steps = scenario_steps(Scenario(storage))
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "retained": RETAINED_PATH, "contribution": CONTRIBUTION_PATH, "linked_id": LINKED_ID, "steps": steps,
               "texts": text_cases()}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL
    assert str(ROOT) not in text and str(ROOT).replace(B, "/") not in text and "Temp" + B not in text
    OUTPUT.write_text(text, encoding="utf-8", newline=NL)
    kinds = {}
    for step in steps:
        result = step["outcome"]
        key = step["op"] + ": " + ("ok" if result[0] == "ok" else result[0] + " " + result[1][:55])
        kinds[key] = kinds.get(key, 0) + 1
    print(len(steps), "steps,", len(vectors["texts"]), "texts")
    for key in sorted(kinds):
        print(" ", kinds[key], ascii(key))


if __name__ == "__main__":
    main()
