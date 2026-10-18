"""PyInstaller entry point for Sylvia GM's standalone authority owner."""

from sylvia_gm.interfaces.owner_process import main

if __name__ == "__main__":
    raise SystemExit(main())
