"""Informational command-line entry point for Sylvia GM."""

from __future__ import annotations

import argparse

from sylvia_gm import __version__


def main() -> None:
    """Show product identity and direct users to the canonical desktop."""
    parser = argparse.ArgumentParser(
        prog="sylvia-gm",
        description=(
            "Sylvia GM is launched through the installed Windows desktop application. "
            "Campaign and provider authority remains in its supervised owner process."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sylvia-gm {__version__}",
    )
    parser.parse_args()
    parser.print_help()


if __name__ == "__main__":
    main()
