# Linux release checklist

Sylvia ships a Linux build of the desktop app beside the Windows one: a single AppImage for
x86_64. Work through these steps on the Linux build machine for each release candidate, in order,
from the clean committed source selected for the release, the same commit as the Windows build.
The [Windows release checklist](windows-release-checklist.md) covers the Windows side.

Commands are for bash in the checkout.

## 1. The build machine

Build on **Ubuntu 22.04 x86_64**. An AppImage carries the build machine's libraries and needs a
C library at least as new as theirs, so the build machine sets the oldest distribution the app
runs on. Ubuntu 22.04 has glibc 2.35, which makes the result usable on Ubuntu 22.04, Debian 12,
Fedora 36 and newer. The build script refuses a machine with a newer glibc.

Set the machine up once:

1. Run the [development setup](linux-development.md#set-up-with-one-command):
   `scripts/setup_linux_dev.sh`.
2. Install the desktop build libraries:

   ```bash
   sudo apt-get install -y --no-install-recommends \
     build-essential pkg-config curl wget file libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev \
     libssl-dev libayatana-appindicator3-dev librsvg2-dev patchelf xdg-utils \
     desktop-file-utils libfuse2 squashfs-tools
   ```

3. Install Rust with [rustup](https://rustup.rs) (stable toolchain) and the Tauri 2 CLI:
   `cargo install tauri-cli --version "^2.0.0" --locked`.

The build downloads Tauri's AppImage bundling tools from GitHub the first time it runs in a
build folder, so the machine needs network access during the build. The release manifest records
one hash over the tools that were downloaded and run.

## 2. Run the checks on Linux

Run the complete local check run on the selected commit
([Run the checks](linux-development.md#run-the-checks)): `scripts/setup_linux_dev.sh --full-run`.
It may be run on any supported Linux machine; it does not have to be the build machine. Expect
`platform` `linux`, `passed` and `complete_gate` true and `unexpected_skips` 0 in `summary.json`.

Also run the desktop shell's own tests on the build machine:

```bash
(cd src-tauri && cargo test --locked)
```

## 3. Release build

In a clean checkout of the selected commit:

```bash
git status --short          # must print nothing; the builder refuses a dirty tree
git rev-parse HEAD          # record this commit
uv sync --locked --all-extras
uv run --locked --all-extras python scripts/check_release_identity.py --forbidden-name-file EXTERNAL_FILE
uv run --locked --all-extras python scripts/build_linux_prerelease.py --dependency-notices-sha256 SHA256 --forbidden-name-file EXTERNAL_FILE
```

`EXTERNAL_FILE` and `SHA256` are the same external literal-name list and notice pin as for the
[Windows release build](windows-release-checklist.md#3-release-build).

The builder writes the AppImage, `release-manifest.json`, `SHA256SUMS.txt`, `UNSIGNED.txt`, the
licence and the notices to `dist/sylvia-gm-<version>-linux-x86_64` unless `--output` names
another new folder. The notices include `licenses/LINUX-BUNDLED-LIBRARIES.txt`, which the builder
writes from the bundle it has just made
([What the Linux build carries](#4-what-the-linux-build-carries-and-leaves-out)). The manifest's format is `sylvia-gm-linux-release-manifest`; besides what the
Windows manifest records, its toolchain section names the build machine's system and glibc. The
`--ubuntu-22-04` and `--ubuntu-24-04` options record qualification states and default to
`pending`. `--allow-newer-glibc` is for trial builds on a newer machine only; such a build does
not start on older distributions and must not be released.

Check the folder afterwards:

```bash
cd dist/sylvia-gm-<version>-linux-x86_64 && sha256sum -c SHA256SUMS.txt
```

## 4. What the Linux build carries and leaves out

- **Same as Windows:** the desktop shell, the bundled authority program, Rulesets and prepared
  campaigns.
- **Left out:** local dictation. Settings says it is not available in the Linux app yet and the
  Dictate button is off. The speech runtime and its notices are not in the bundle.
- **Not available:** in-app updates. The update service reports the Linux target as unsupported;
  a new version is a new AppImage.
- **Saved provider keys** are encrypted with a per-user key file
  ([Provider keys](linux-development.md#provider-keys-on-shared-or-rented-machines)). There is no
  desktop keyring in front of it yet.
- **Bundled system libraries:** the AppImage carries the web view (WebKitGTK), GTK and the
  libraries they need, copied from the build machine's packages. `dependency-notices.txt`
  describes the Python and Rust components, not these. The builder therefore writes
  `licenses/LINUX-BUNDLED-LIBRARIES.txt` for every build: each package behind a bundled file,
  with its version, its Ubuntu source package and its copyright file, where to get the source and
  how a person can replace a library. The build stops if a bundled shared library belongs to no
  installed package or a package has no copyright file. The file ships in the release folder and
  the manifest records its hash; publish it with the AppImage. It can also be written for any
  unpacked AppImage with `scripts/build_linux_bundled_notices.py`.
- **Not covered by either notices file:** the libraries built into the Python runtime of the
  Linux build.

## 5. Installed-application pass

Run the built AppImage, not a development build. A person needs FUSE 2 (`libfuse2`) to start an
AppImage directly; `--appimage-extract-and-run` starts it without.

The pass can be driven on a hidden X display, without a desktop session, which is how an agent
runs it: start `Xvfb`, a window manager and one D-Bus session, set `GDK_BACKEND=x11` and
`WEBKIT_DISABLE_COMPOSITING_MODE=1` (a virtual display leaves parts of the window unpainted
otherwise), give the app an empty `HOME`, and act on it with `xdotool` while taking screenshots.
Use a local stand-in for the model service, so no paid model call is made.

Check, on Ubuntu 22.04 and on Ubuntu 24.04:

1. First start shows Home; the data folder is `~/.local/share/com.pneuma-io.sylvia-gm` with
   mode 700.
2. Settings: add an API-key connection, find its models, save one. `config.yaml` in the data
   folder and `~/.local/state/sylvia-gm/credential-key` have mode 600, and no saved file contains
   the key in plain text. The storage explanation names Linux, not Windows.
3. Start a prepared campaign; the Game Master's opening arrives; send a player message.
4. Settings, Campaign data: export the campaign (the confirmation box appears, the save dialog
   opens in the home folder, the file is written); recover a campaign from an exported file.
5. Start the app a second time while it runs: the second copy exits and one window remains.
6. Close the window: the app exits with code 0 and leaves no process behind. Start it again: the
   campaign is still there.

A hidden display does not show how the app looks and behaves in a real desktop session. Look at
these in one, on at least one release candidate per release: drawing with the desktop's own
compositing (including a Wayland session), the open drop-down lists and file dialogs in the
desktop's theme, starting from the application menu (which decides whether separately installed
programs such as the Codex runtime are found), the clipboard, and detached panel windows.

## 6. Publish

Publishing is the release owner's step and is the same for both systems: attach the AppImage,
`release-manifest.json`, `SHA256SUMS.txt` and `LINUX-BUNDLED-LIBRARIES.txt` to the GitHub release
that holds the Windows files, marked Pre-release. Say in the release notes which distributions the build was checked on and
what the Linux build leaves out.

## 7. Record

Record the commit, the build machine's system and glibc from the manifest, the check run's
`summary.json`, the installed-application pass with its screenshots, and anything not checked.
