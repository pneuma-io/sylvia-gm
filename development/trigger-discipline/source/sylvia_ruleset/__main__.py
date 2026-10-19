from __future__ import annotations

import argparse
from pathlib import Path

from .builder import build_draft, publish_candidate


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sylvia_ruleset",
        description="Build and publish the Trigger Discipline Sylvia GM Ruleset candidate.",
    )
    parser.add_argument("command", choices=("build", "publish", "verify"))
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--sylvia-root", type=Path)
    parser.add_argument("--source-commit")
    parser.add_argument("--include-test-tools", action="store_true")
    parser.add_argument("--allow-worktree-sources", action="store_true",
                        help="Build an explicitly marked worktree candidate with per-file source hashes.")
    args = parser.parse_args()

    result = build_draft(
        args.workspace, source_commit=args.source_commit,
        include_test_tools=args.include_test_tools or args.command == "verify",
        allow_worktree_sources=args.allow_worktree_sources,
    )
    if args.command == "build":
        print(result["draft"])
        print(result["source_sha256"])
        print(result["parameter_sha256"])
        return 0
    if args.sylvia_root is None:
        parser.error(f"{args.command} requires --sylvia-root")
    if args.command == "verify":
        from .qualification import qualify_candidate

        qualified = qualify_candidate(args.workspace, args.sylvia_root)
        print(qualified["report"])
        print(qualified["status"])
        return 0
    published = publish_candidate(args.workspace, args.sylvia_root)
    print(published["artifact"])
    print(published["closure_sha256"])
    print(published["package_sha256"])
    print(published["sylvia_commit"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
