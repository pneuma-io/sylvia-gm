"""Notices for the system libraries a Linux AppImage carries.

The package database is replaced by a synthetic one here, so these cases run on any system.
The real writer is exercised by the Linux release build on the build machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import build_linux_bundled_notices as bundled

# A synthetic package database: which package owns which path, and what each reports.
OWNERS = {
    "/usr/lib/x86_64-linux-gnu/libexample.so.1.2.3": "libexample1",
    "/usr/lib/x86_64-linux-gnu/libexample.so.1": "libexample1",
    "/usr/lib/x86_64-linux-gnu/libexample.so": "libexample-dev",
    "/usr/lib/x86_64-linux-gnu/gio/modules/libgiosynthetic.so": "synthetic-networking",
    "/usr/share/glib-2.0/schemas/org.synthetic.gschema.xml": "synthetic-schemas",
    "/usr/lib/x86_64-linux-gnu/libshared.so.4": "libshared4",
    "/usr/lib/i386-linux-gnu/libshared.so.4": "libshared4",
    # Recorded under /lib, which is a link to /usr/lib on the build machine.
    "/lib/x86_64-linux-gnu/libmerged.so.1": "libmerged1",
    "/lib/x86_64-linux-gnu/libmerged.so.1.0.4": "libmerged1",
    "/opt/unrelated/libmerged.so.1.0.4": "unrelated-copy",
}
LINKS = {
    "/usr/lib/x86_64-linux-gnu/libexample.so": "/usr/lib/x86_64-linux-gnu/libexample.so.1.2.3",
    "/usr/lib/x86_64-linux-gnu/libexample.so.1": "/usr/lib/x86_64-linux-gnu/libexample.so.1.2.3",
    "/lib/x86_64-linux-gnu/libmerged.so.1": "/usr/lib/x86_64-linux-gnu/libmerged.so.1.0.4",
    "/lib/x86_64-linux-gnu/libmerged.so.1.0.4": "/usr/lib/x86_64-linux-gnu/libmerged.so.1.0.4",
}
FIELDS = {
    "libexample1": "1.2.3-4ubuntu1\texample\t1.2.3-4ubuntu1",
    "synthetic-networking": "2.72.0-1\tsynthetic-networking\t2.72.0-1",
    "synthetic-schemas": "42.0-1\tsynthetic-schemas\t42.0-1",
    "libshared4": "4.0-1build2\tshared\t4.0-1",
    "libmerged1": "1.0.4-2\tmerged\t1.0.4-2",
}
COPYRIGHT = {
    "libexample1": "Format: synthetic\nLicense: LGPL-2.1+\n Example text.\n",
    "synthetic-networking": "License: LGPL-2.1+\n",
    "synthetic-schemas": "License: LGPL-2.1+\n",
    "libshared4": "License: Expat\n",
    "libmerged1": "License: BSD-3-clause\n",
}


def _run(command):
    if command[:2] == ["dpkg-query", "-S"]:
        pattern = command[2]
        if pattern.startswith("*/"):
            name = pattern[2:]
            paths = sorted(path for path in OWNERS if path.endswith("/" + name))
        else:
            paths = [pattern] if pattern in OWNERS else []
        # Two packages may be reported on one line, as dpkg does for shared paths.
        return "".join(f"{OWNERS[path]}:amd64: {path}\n" for path in paths)
    if command[:2] == ["dpkg-query", "-W"]:
        return FIELDS.get(command[-1], "")
    raise AssertionError(command)


def _read(path: Path):
    return COPYRIGHT.get(path.parent.name)


def _appdir(tmp_path: Path, names=("libexample.so", "libshared.so.4")) -> Path:
    appdir = tmp_path / "Synthetic.AppDir"
    for name in names:
        target = appdir / "usr/lib" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic")
    return appdir


def _collect(appdir: Path, **changes):
    options = {"run": _run, "realpath": lambda path: LINKS.get(path, path), "read": _read}
    options.update(changes)
    return bundled.collect(appdir, "Synthetic App", **options)


def test_a_library_bundled_under_its_link_name_is_traced_to_the_package_that_ships_it(tmp_path):
    packages = _collect(_appdir(tmp_path))
    assert [package.name for package in packages] == ["libexample1", "libshared4"]
    example = packages[0]
    # The development package only owns the link; the runtime package ships the library.
    assert example.files == ("libexample.so",)
    assert (example.version, example.source, example.source_version) == (
        "1.2.3-4ubuntu1", "example", "1.2.3-4ubuntu1",
    )
    assert example.copyright == COPYRIGHT["libexample1"]
    # A library present for two architectures still names one package.
    assert packages[1].source_version == "4.0-1"


def test_a_library_recorded_under_a_linked_folder_is_still_traced_to_its_package(tmp_path):
    # Found on the real build machine: the database knows /lib/..., the file is /usr/lib/...
    def resolve(path):
        return LINKS.get(path, path)

    packages = _collect(_appdir(tmp_path, ("libmerged.so.1",)))
    assert [(package.name, package.files) for package in packages] == [
        ("libmerged1", ("libmerged.so.1",))
    ]
    # A file of the same name that another package ships elsewhere is a different file.
    assert OWNERS["/opt/unrelated/libmerged.so.1.0.4"] == "unrelated-copy"
    assert bundled.owning_package("libmerged.so.1", run=_run, realpath=resolve) == "libmerged1"


def test_modules_and_data_files_are_listed_and_the_apps_own_folder_is_left_out(tmp_path):
    appdir = _appdir(tmp_path, ("libexample.so.1", "gio/modules/libgiosynthetic.so"))
    schema = appdir / "usr/share/glib-2.0/schemas/org.synthetic.gschema.xml"
    schema.parent.mkdir(parents=True)
    schema.write_text("<schemalist/>", encoding="utf-8")
    (appdir / "usr/share/glib-2.0/schemas/gschemas.compiled").write_bytes(b"generated")
    own = appdir / "usr/lib/Synthetic App/licenses/LICENSE"
    own.parent.mkdir(parents=True)
    own.write_text("the application's own licence", encoding="utf-8")
    (appdir / "usr/lib/Synthetic App/libprivate.so").write_bytes(b"would be unaccounted")
    (appdir / "usr/bin").mkdir()
    (appdir / "usr/bin/synthetic-app").write_bytes(b"the application")
    packages = _collect(appdir)
    assert {package.name: package.files for package in packages} == {
        "libexample1": ("libexample.so.1",),
        "synthetic-networking": ("libgiosynthetic.so",),
        "synthetic-schemas": ("org.synthetic.gschema.xml",),
    }


def test_a_shared_library_no_package_ships_stops_the_notices(tmp_path):
    appdir = _appdir(tmp_path, ("libexample.so", "libmystery.so.3", "plugins/libodd.so"))
    with pytest.raises(bundled.BundledNoticesError) as refused:
        _collect(appdir)
    assert "usr/lib/libmystery.so.3" in str(refused.value)
    assert "usr/lib/plugins/libodd.so" in str(refused.value)
    assert "libexample" not in str(refused.value)


def test_a_package_without_a_version_or_a_copyright_file_stops_the_notices(tmp_path):
    appdir = _appdir(tmp_path)
    with pytest.raises(bundled.BundledNoticesError, match="libshared4 has no copyright file"):
        _collect(appdir, read=lambda path: None if path.parent.name == "libshared4" else "text")
    with pytest.raises(bundled.BundledNoticesError, match="has no copyright file"):
        _collect(appdir, read=lambda _path: "  \n")

    def silent(command):
        return "" if command[:2] == ["dpkg-query", "-W"] else _run(command)

    with pytest.raises(bundled.BundledNoticesError, match="does not report its version"):
        _collect(appdir, run=silent)
    with pytest.raises(bundled.BundledNoticesError, match="holds no library files"):
        _collect(tmp_path / "empty.AppDir")


def test_the_notices_name_every_package_its_source_and_its_copyright_text(tmp_path):
    packages = _collect(_appdir(tmp_path))
    text = bundled.render(packages, build_system="Synthetic Linux 1.0")
    assert text.startswith("Sylvia GM for Linux: notices for the bundled system libraries\n")
    assert "Synthetic Linux 1.0" in text
    assert "Packages: 2\n" in text
    assert "  libexample1  1.2.3-4ubuntu1  (source: example 1.2.3-4ubuntu1)\n" in text
    assert "  libshared4   4.0-1build2  (source: shared 4.0-1)\n" in text
    assert "libexample1 1.2.3-4ubuntu1\nSource package: example 1.2.3-4ubuntu1\n" in text
    assert "Bundled files: libexample.so\n" in text
    assert COPYRIGHT["libexample1"].rstrip() in text and "License: Expat" in text
    # What a reader needs to act on the notices: the source, and how to swap a library.
    assert "apt-get source <source package>=<version>" in text
    assert "--appimage-extract" in text
    assert "do not apply to the Linux build" in text
    assert text.endswith("\n") and "\r" not in text


def test_the_writer_is_documented_as_a_command(capsys):
    with pytest.raises(SystemExit):
        bundled.main(["--help"])
    usage = capsys.readouterr().out
    for option in ("--appdir", "--output", "--own-folder", "--build-system"):
        assert option in usage
    assert bundled.NOTICES_NAME == "LINUX-BUNDLED-LIBRARIES.txt"
