"""Check local links in touched Markdown; external sites are outside this PR gate."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def check_links(root, documents):
    failures = []
    for relative in documents:
        document = root / relative
        if not document.is_file():
            # A deleted document has no postimage links to check.
            continue
        text = re.sub(r"```.*?```", "", document.read_text(encoding="utf-8"), flags=re.S)
        for target in re.findall(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text):
            target = target.strip("<>")
            link = urlsplit(target)
            if link.scheme or link.netloc or target.startswith("#"):
                continue
            resolved = (document.parent / unquote(link.path)).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                failures.append(f"{relative}: missing local link {target}")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.selection_manifest.read_text(encoding="utf-8"))
    failures = check_links(Path(__file__).resolve().parents[1], plan["documents"])
    for failure in failures:
        print(failure)
    label = "FAIL" if failures else "PASS"
    print(f"Touched-document local links: {label} ({len(plan['documents'])} documents)")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
