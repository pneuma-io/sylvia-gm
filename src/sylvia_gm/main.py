"""Product information and explicit local browser launch."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sylvia_gm import __version__


def main() -> None:
    """Show product identity or run the selected loopback WebUI data root."""
    parser = argparse.ArgumentParser(
        prog="sylvia-gm",
        description=(
            "Use the installed Windows desktop or launch the source WebUI "
            "with an explicit data root. "
            "Campaign and provider authority remains in its supervised owner process."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sylvia-gm {__version__}",
    )
    parser.add_argument("--webui", action="store_true", help="Run the loopback browser UI")
    parser.add_argument("--data-root", type=Path, help="Explicit local Sylvia data directory")
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 selects a free port")
    parser.add_argument("--webui-remote-origin", help="Exact private Tailscale HTTPS origin")
    parser.add_argument(
        "--webui-access-key-file",
        type=Path,
        help="User-managed random access-key file for private HTTPS mode",
    )
    args = parser.parse_args()
    remote_selected = args.webui_remote_origin is not None or args.webui_access_key_file is not None
    if remote_selected and not args.webui:
        parser.error("Private HTTPS options require --webui")
    if (args.webui_remote_origin is None) != (args.webui_access_key_file is None):
        parser.error(
            "Private HTTPS requires both --webui-remote-origin and --webui-access-key-file"
        )
    if not args.webui:
        parser.print_help()
        return
    if args.data_root is None:
        parser.error("--webui requires --data-root; choose a dataset explicitly")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be from 0 to 65535")
    from sylvia_gm.interfaces.webui import RemoteAccess, WebUIError, serve

    try:
        remote = (
            RemoteAccess.from_file(args.webui_remote_origin, args.webui_access_key_file)
            if remote_selected
            else None
        )
        asyncio.run(serve(args.data_root, args.port, remote=remote))
    except KeyboardInterrupt:
        pass
    except (WebUIError, OSError) as error:
        parser.exit(1, f"WebUI could not start: {error}\n")


if __name__ == "__main__":
    main()
