"""Generate reference vectors for sylvia_app's `BundledPackages::bundled_campaign_content_catalog`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_content_catalog_vectors.py

Records `bundled_campaign_content_catalog()` for:
- the repository's bundled content;
- synthetic bundled Plan collections, swapped in through `sys._MEIPASS`. A frozen build reads its
  Plan collection from there with no fallback, while the Ruleset, Setting and retained Rift Plan
  fall back to the checkout. The collections are: an empty catalogue, one copied Plan, the
  repository's catalogue reversed, a catalogue that is not a list, a row naming a missing Plan, and
  no catalogue at all.
A path in an error is masked: the scratch root becomes `<ROOT>`, with `/` separators after it.
Every cache in `bundled_packages` and `bundled_campaign_plans` is cleared between runs. Each run
also calls twice, to show the second call returns the same rows after the caller edits the first.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application import bundled_campaign_plans as bcp  # noqa: E402
from sylvia_gm.application import bundled_packages as bundled  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402

OUTPUT = Path(__file__).with_name("content_catalog_vectors.json")
NL = chr(10)
B = chr(92)
PLANS = ROOT / "campaign-plans"


def thaw(value):
    return json.loads(canonical_json_bytes(value))


def clear_caches():
    for module in (bundled, bcp):
        for value in vars(module).values():
            if callable(getattr(value, "cache_clear", None)):
                value.cache_clear()


def mask(text, roots):
    for root in roots:
        for form in (str(root).replace(B, B + B), str(root), str(root).replace(B, "/")):
            text = text.replace(form, "<ROOT>")
    parts = text.split("<ROOT>")
    for index in range(1, len(parts)):
        path, quote, rest = parts[index].partition("'")
        parts[index] = path.replace(B + B, "/").replace(B, "/") + quote + rest
    return "<ROOT>".join(parts)


def outcome(frozen_root, roots=()):
    clear_caches()
    if frozen_root is None:
        sys.__dict__.pop("_MEIPASS", None)
    else:
        sys._MEIPASS = str(frozen_root)
    try:
        try:
            first = bundled.bundled_campaign_content_catalog()
        except Exception as error:  # every class is behaviour
            return [type(error).__name__, mask(str(error), roots)]
        recorded = thaw(list(first))
        first[0]["title"] = "edited by the caller"
        first[-1].clear()
        again = thaw(list(bundled.bundled_campaign_content_catalog()))
        assert again == recorded, "the cached catalog changed"
        return ["ok", recorded]
    finally:
        sys.__dict__.pop("_MEIPASS", None)
        clear_caches()


def collection(scratch, name, catalogue, plans=()):
    root = scratch / name
    (root / "campaign-plans").mkdir(parents=True)
    if catalogue is not None:
        (root / "campaign-plans" / "catalogue.json").write_text(json.dumps(catalogue), encoding="utf-8")
    for plan in plans:
        shutil.copytree(PLANS / plan, root / "campaign-plans" / plan)
    return root


def main() -> None:
    real = json.loads((PLANS / "catalogue.json").read_text(encoding="utf-8"))
    single = next(row for row in real if row["id"] == "last-cart-through")
    cases = [{"name": "repository", "collection": None, "outcome": outcome(None)}]
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        variants = {
            "empty": ([], ()),
            "single": ([single], ("last-cart-through",)),
            "reversed": (list(reversed(real)), tuple(row["id"] for row in real)),
            "not_a_list": ({"id": "last-cart-through"}, ("last-cart-through",)),
            "missing_plan": ([single], ()),
            "no_catalogue": (None, ("last-cart-through",)),
        }
        for name, (catalogue, plans) in variants.items():
            root = collection(scratch, name, catalogue, plans)
            cases.append({"name": name, "collection": {"catalogue": catalogue, "plans": list(plans)},
                          "outcome": outcome(root, (scratch, scratch.resolve()))})
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL, encoding="utf-8",
                      newline=NL)
    for case in cases:
        result = case["outcome"]
        print(case["name"], result[0], len(result[1]) if result[0] == "ok" else ascii(result[1])[:100])


if __name__ == "__main__":
    main()
