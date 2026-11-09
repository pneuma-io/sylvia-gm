"""Bundle only the default Windows x64 PortAudio runtime used by the speech worker."""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

hiddenimports = ["_sounddevice_data"]
binaries = collect_dynamic_libs("_sounddevice_data", search_patterns=["libportaudio64bit.dll"])
datas = collect_data_files("_sounddevice_data", includes=["portaudio-binaries/README.md"])
if len(binaries) != 1:
    raise RuntimeError("The pinned Windows x64 PortAudio runtime is missing")
