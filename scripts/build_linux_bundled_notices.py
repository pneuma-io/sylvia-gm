"""Write the notices for the system libraries a Linux AppImage carries.

An AppImage is built from the build machine's own packages: the web view, GTK and everything
they need are copied into it. This lists every shared library in the bundle with the Debian
package it came from, that package's version and source package, and the package's copyright
file, so the notices describe exactly what one build carries.

The build machine must be Debian or Ubuntu: the packages are found with dpkg-query.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

NOTICES_NAME = "LINUX-BUNDLED-LIBRARIES.txt"
_SHARED_OBJECT = re.compile(r"\.so(\.[0-9]+)*$")
_RULE = "=" * 72


class BundledNoticesError(ValueError):
    """The bundle holds something the notices cannot account for."""


@dataclass(frozen=True, slots=True)
class BundledPackage:
    name: str
    version: str
    source: str
    source_version: str
    files: tuple[str, ...]
    copyright: str


def _run(command: Sequence[str]) -> str:
    """Standard output of a query, or an empty string when it finds nothing."""

    completed = subprocess.run(list(command), capture_output=True, text=True, check=False)
    return completed.stdout if completed.returncode == 0 else ""


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _owners(output: str) -> list[tuple[str, str]]:
    """(package, path) pairs from dpkg-query -S output; one line may name several packages."""

    pairs = []
    for line in output.splitlines():
        names, separator, path = line.partition(": ")
        if not separator or not path.startswith("/"):
            continue
        for name in names.split(","):
            pairs.append((name.strip().split(":", maxsplit=1)[0], path.strip()))
    return pairs


def bundled_files(appdir: Path, own_folder: str) -> list[Path]:
    """Every regular file under the bundle's library and shared-data folders, except the
    application's own resources."""

    files = []
    for root in (appdir / "usr/lib", appdir / "usr/share/glib-2.0"):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if (appdir / "usr/lib" / own_folder) in path.parents:
                continue
            files.append(path)
    if not files:
        raise BundledNoticesError("The bundle folder holds no library files")
    return files


def owning_package(
    name: str,
    *,
    run: Callable[[Sequence[str]], str] = _run,
    realpath: Callable[[str], str] = os.path.realpath,
) -> str | None:
    """The package that ships the real file behind a library name.

    A bundle often holds a library under its unversioned name, which a development package
    owns as a link; following the link finds the package that ships the library itself.
    """

    candidates = set()
    for _package, path in _owners(run(["dpkg-query", "-S", f"*/{name}"])):
        if Path(path).name != name:
            continue
        real = realpath(path)
        owners = [
            package
            for package, owned in _owners(run(["dpkg-query", "-S", real]))
            if owned == real
        ]
        if not owners:
            # The package database may record the file under a folder that is itself a link
            # (/lib for /usr/lib), so the real path is not a name it knows. Find the file by
            # name and keep the entries that are the same file.
            owners = [
                package
                for package, owned in _owners(run(["dpkg-query", "-S", f"*/{Path(real).name}"]))
                if realpath(owned) == real
            ]
        candidates.update(owners)
    return min(candidates) if candidates else None


def collect(
    appdir: Path,
    own_folder: str,
    *,
    run: Callable[[Sequence[str]], str] = _run,
    realpath: Callable[[str], str] = os.path.realpath,
    read: Callable[[Path], str | None] = _read,
    documents: Path = Path("/usr/share/doc"),
) -> list[BundledPackage]:
    """The packages behind the bundle's files. A shared library that no package ships, or a
    package without a copyright file, stops the build: the notices would be incomplete."""

    by_package: dict[str, set[str]] = {}
    unaccounted = []
    for path in bundled_files(appdir, own_folder):
        package = owning_package(path.name, run=run, realpath=realpath)
        if package is not None:
            by_package.setdefault(package, set()).add(path.name)
        elif _SHARED_OBJECT.search(path.name):
            unaccounted.append(path.relative_to(appdir).as_posix())
    if unaccounted:
        raise BundledNoticesError(
            "No installed package ships these bundled libraries: " + ", ".join(unaccounted)
        )
    packages = []
    for name in sorted(by_package):
        fields = run(
            ["dpkg-query", "-W", "-f", "${Version}\t${source:Package}\t${source:Version}", name]
        ).split("\t")
        if len(fields) != 3 or not all(field.strip() for field in fields):
            raise BundledNoticesError(f"The package {name} does not report its version")
        text = read(documents / name / "copyright")
        if text is None or not text.strip():
            raise BundledNoticesError(f"The package {name} has no copyright file")
        packages.append(
            BundledPackage(
                name=name,
                version=fields[0].strip(),
                source=fields[1].strip(),
                source_version=fields[2].strip(),
                files=tuple(sorted(by_package[name])),
                copyright=text.rstrip() + "\n",
            )
        )
    if not packages:
        raise BundledNoticesError("No bundled library could be traced to a package")
    return packages


def render(packages: Sequence[BundledPackage], *, build_system: str) -> str:
    """The notices file: what it covers, where the source is, then each package in full."""

    lines = [
        "Sylvia GM for Linux: notices for the bundled system libraries",
        "",
        "The Sylvia GM AppImage carries the system libraries it needs to run: the web view",
        "(WebKitGTK), GTK and the libraries they depend on. They were copied from the packages",
        f"of the system the AppImage was built on, {build_system}. The bundling tool changes",
        "them only so that they load from inside the AppImage: it removes debugging symbols and",
        "sets where each library looks for the others.",
        "",
        "This file lists every one of those packages with its version, the bundled files that",
        "came from it and its copyright file as the distribution ships it. Sylvia GM's own",
        "licence and the notices for its Python and Rust components are in LICENSE and",
        "dependency-notices.txt beside this file. The parts of dependency-notices.txt about the",
        "libffi Windows source fork and the Microsoft WebView2 SDK describe the Windows build",
        "and do not apply to the Linux build. The libraries built into the Python runtime of",
        "the Linux build are not listed in either file.",
        "",
        "Source code. Each package below names its Ubuntu source package and version. The",
        "source is available from the Ubuntu archive, for example with",
        "\"apt-get source <source package>=<version>\" on that Ubuntu release, or from",
        "https://launchpad.net/ubuntu/+source/<source package>/<version>.",
        "",
        "Replacing a library. The libraries are separate shared objects inside the AppImage,",
        "under usr/lib. Run the AppImage with --appimage-extract to unpack it, replace a",
        "library with a compatible build of your own, and start the unpacked AppRun.",
        "",
        f"Packages: {len(packages)}",
        "",
    ]
    width = max(len(package.name) for package in packages)
    for package in packages:
        lines.append(
            f"  {package.name.ljust(width)}  {package.version}"
            f"  (source: {package.source} {package.source_version})"
        )
    for package in packages:
        lines += [
            "",
            _RULE,
            f"{package.name} {package.version}",
            f"Source package: {package.source} {package.source_version}",
            "Bundled files: " + ", ".join(package.files),
            _RULE,
            "",
            package.copyright.rstrip(),
        ]
    return "\n".join(lines) + "\n"


def write_notices(
    appdir: Path, output: Path, *, own_folder: str, build_system: str
) -> list[BundledPackage]:
    packages = collect(appdir, own_folder)
    output.write_text(render(packages, build_system=build_system), encoding="utf-8", newline="\n")
    return packages


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appdir", type=Path, required=True, help="The unpacked AppImage folder")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--own-folder", default="Sylvia GM", help="The app's folder in usr/lib")
    parser.add_argument("--build-system", required=True, help="For example: Ubuntu 22.04.5 LTS")
    arguments = parser.parse_args(argv)
    packages = write_notices(
        arguments.appdir,
        arguments.output,
        own_folder=arguments.own_folder,
        build_system=arguments.build_system,
    )
    print(f"{len(packages)} packages written to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
