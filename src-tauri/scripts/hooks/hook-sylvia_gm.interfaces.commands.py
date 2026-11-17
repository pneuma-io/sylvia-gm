"""Collect command topics selected dynamically by the owner registry."""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("sylvia_gm.interfaces.commands", on_error="raise")
