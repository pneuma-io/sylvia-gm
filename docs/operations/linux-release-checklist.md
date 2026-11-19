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
     desktop-file-utils libfuse2 squashfs-tools \
     gstreamer1.0-plugins-base gstreamer1.0-plugins-good
   ```

   The last two hold the GStreamer plug-ins the AppImage carries for sound (see
   [section 4](#4-what-the-linux-build-carries-and-leaves-out)); the build stops if one is
   missing.

3. Install Rust with [rustup](https://rustup.rs) (stable toolchain) and the Tauri 2 CLI:
   `cargo install tauri-cli --version "^2.0.0" --locked`.
4. For the [installed-application check](#5-installed-application-pass), install a hidden
   display, a window manager, the input and screenshot tools and the WebDriver pieces:

   ```bash
   sudo apt-get install -y --no-install-recommends \
     xvfb openbox dbus xdotool wmctrl imagemagick webkit2gtk-driver
   cargo install tauri-driver --locked
   ```

   `webkit2gtk-driver` must have the same WebKitGTK version as the one the AppImage carries,
   which it does on the build machine because the AppImage is made from its packages.

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
- **Sound:** the web view plays Sylvia's shot sound effects through GStreamer, and the AppImage
  tells GStreamer to look for plug-ins only inside itself. The release script therefore hands
  the bundler a folder with just the plug-ins sound needs (`MEDIA_PLUGINS` in
  `scripts/build_linux_prerelease.py`: the web view's audio pipeline and the PulseAudio output,
  which PipeWire desktops also provide). Without that folder the bundler copies every plug-in on
  the build machine, including some that bring GPL or Berkeley DB libraries for features the
  app never uses. A system with neither PulseAudio nor PipeWire plays no sound; the app works
  otherwise.
- **Not available:** in-app updates. The update service reports the Linux target as unsupported;
  a new version is a new AppImage.
- **Saved provider keys** are encrypted with a per-user key file
  ([Provider keys](linux-development.md#provider-keys-on-shared-or-rented-machines)). There is no
  desktop keyring in front of it yet.
- **Claude subscription:** not bundled. As on Windows, the Claude page downloads Sylvia's own copy
  of Claude Code from Anthropic (`linux-x64`, or `-musl` on a musl system). Anthropic does not
  code-sign Linux builds, so Sylvia first checks the release's signed `manifest.json` with
  `gpgv` against Anthropic's published release key, then the download's SHA-256. Without `gpgv`
  (GnuPG) nothing is installed and the page asks for GnuPG. The sign-in opens the desktop's
  browser.
- **ChatGPT account:** the Codex runtime is installed separately and found on `PATH`. An app
  started from the desktop's menu can have a shorter `PATH` than a terminal; the missing-runtime
  message says how to fix that.
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

Run the built AppImage, not a development build. The copy in the release folder is marked
executable; a downloaded copy is not, so a person runs `chmod +x` on it first, and the release
notes should say so. A person needs FUSE 2 (`libfuse2`) to start an AppImage directly;
`--appimage-extract-and-run` starts it without.

On a machine that runs Linux inside Windows (WSL), remove the Windows folders from `PATH` before
starting the app or a build. WSL adds them, and the app would otherwise find and start Windows
programs, such as a Codex runtime with that person's own sign-in.

One command runs the pass on a hidden display, with no person, no desktop session, no paid model
call and no sign-in, on a machine prepared as [section 1](#1-the-build-machine) describes
(step 4):

```bash
python3 scripts/qa/linux_installed_check.py --appimage PATH/TO/APPIMAGE --output NEW_FOLDER
```

It starts `Xvfb`, a window manager, its own D-Bus session and a local stand-in for the model
service (`scripts/qa/standin_model_endpoint.py`), gives the app empty private home folders and
drives it through WebDriver (`tauri-driver` in front of `WebKitWebDriver`), which finds page
elements by id and reads their text. Native GTK message boxes and file dialogs are answered with
`xdotool` key presses. The app gets `GDK_BACKEND=x11` and `WEBKIT_DISABLE_COMPOSITING_MODE=1` (a
virtual display leaves parts of the window unpainted otherwise) and an allow-listed environment:
the command removes Windows folders from `PATH` (a WSL machine adds them), never passes
`WAYLAND_DISPLAY`, provider keys or `SYLVIA_GM_*` settings, and refuses to start if it cannot
make the environment safe. It writes only to the output folder and its own temporary folder
(`--work-dir`, by default the system temporary folder), removes that folder with the unpacked
app and the private homes at the end (`--keep-work` keeps it), and stops every process it
started, by process ID, never by name, so other sessions on the machine are left alone. The
output folder receives `report.md`, `report.json`, screenshots and logs. The command exits 0
only if every step that ran passed; a failed step does not stop later steps that can still run.

It checks, in one run:

1. First start shows Home; the data folder is `~/.local/share/com.pneuma-io.sylvia-gm` with
   mode 700.
2. Settings, Models: add the stand-in as an API-key connection (Custom OpenAI-Compatible), find
   its model, enter its context size under Advanced model settings (Sylvia does not take a custom
   service's listed maximum as its loaded limit), save it and make it the default.
   `config.yaml` in the data folder and `~/.local/state/sylvia-gm/credential-key` have mode 600,
   no saved file contains the key in plain text, the stand-in received the right key, and the
   storage explanation names Linux, not Windows.
3. Start a prepared campaign; the Game Master's opening arrives.
4. Send a player message; the turn, which offers the Game Master's tools, completes with a reply.
5. Import a made-up character card (Settings, Packages) and get a reply in Character chat.
6. With the campaign open, Settings, Campaign data: export it. The confirmation box appears,
   the save dialog opens in the home folder and the file is written. No copy of the archive is
   left in the data folder's `exports/`.
7. Start the app a second time while it runs: the second copy exits with code 0 and one window
   remains.
8. Close the window: the app exits with code 0 and leaves no process behind.
9. Start it again on the same home: the campaign is still listed.
10. In an empty profile, recover the exported file: the campaign appears and opens with its
    story.
11. A copy started on the same data folder through a separate D-Bus session shows the "selected
    data unavailable" message box.
12. Dictation says it is not available on Linux and About says in-app updates are not
    supported. The Claude page's answer to "Check Claude Code" is recorded, not judged; nothing is
    installed or signed in.
13. A copy on a fresh profile whose owner process is stopped from outside: closing its window
    shows the "owner close failed" box, and after OK the app exits with code 1 and leaves no
    process behind.

Run it on Ubuntu 22.04 and on Ubuntu 24.04, and look through the screenshots as well as the
result.

A hidden display does not show how the app looks and behaves in a real desktop session. Look at
these in one, on at least one release candidate per release: drawing with the desktop's own
compositing (including a Wayland session), the open drop-down lists and file dialogs in the
desktop's theme, starting from the application menu (which decides whether separately installed
programs such as the Codex runtime are found), the clipboard, and detached panel windows. The
scripted pass installs nothing and signs in nowhere, so also use **Install Claude Code for Sylvia**
and **Sign in with Claude Code** on the Claude page in that session: the download passes its
signature and checksum checks and the sign-in opens the desktop's browser.

## 6. Publish

Publishing is the release owner's step and is the same for both systems: attach the AppImage,
`release-manifest.json`, `SHA256SUMS.txt` and `LINUX-BUNDLED-LIBRARIES.txt` to the GitHub release
that holds the Windows files, marked Pre-release. Say in the release notes which distributions
the build was checked on, what the Linux build leaves out
([section 4](#4-what-the-linux-build-carries-and-leaves-out)), and how a person starts a
downloaded AppImage: `chmod +x` first, and FUSE 2 or `--appimage-extract-and-run`
([section 5](#5-installed-application-pass)).

## 7. Record

Record the commit, the build machine's system and glibc from the manifest, the check run's
`summary.json`, the installed-application pass with its screenshots, and anything not checked.
