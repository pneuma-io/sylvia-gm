"""PyInstaller entry point for Sylvia GM's standalone authority owner."""

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--desktop-update-helper":
        if len(sys.argv) != 2:
            raise SystemExit(2)
        from sylvia_gm.application.desktop_update.helper import main

        raise SystemExit(main())
    if len(sys.argv) > 1 and sys.argv[1] == "--desktop-update-identity":
        if len(sys.argv) != 2:
            raise SystemExit(2)
        from sylvia_gm.application.desktop_update.identity import main

        raise SystemExit(main())
    if len(sys.argv) > 1 and sys.argv[1] == "--dictation-worker":
        from sylvia_gm.local_dictation_process import main

        raise SystemExit(main(sys.argv[2:]))
    from sylvia_gm.interfaces.owner_process import main

    raise SystemExit(main())
