# Linux feasibility for Sylvia GM

Recorded: 2026-09-14
Scope: [AH-08](../../.scratch/astra-hardening-2026-09-14/issues/08-establish-linux-feasibility-and-distribution-options.md)
Source: main at b251b1dddd98201287e253beba6b1a0e3d6905b4 with the existing uncommitted working changes.
Status: Research and isolated backend probes complete; native desktop and distribution unverified.

**A native Linux version looks feasible with the existing Python, SQLite and Tauri architecture.
It needs a real saved-data port before it can be useful. My recommendation is a bounded native
prototype, followed by the Alpha decision. AppImage is the first distribution format I would try.**

The most useful new finding is that backup is a fresh-start requirement too. After correcting only
a Windows-specific import in an isolated copy, the backend could open its database and persist a
simple synthetic campaign. Creating a brand-new Builder chat failed because preparing the newer
database schema calls the Windows-only backup routine. Removing the import or hiding backup
controls cannot produce a usable Linux release.

## What was actually checked

The local environment is Ubuntu 24.04.4 LTS, x86_64, under WSL, with Python 3.12.3 and SQLite 3.45.1.
A WSLg directory is present. The native temporary filesystem reported ext4; the Windows checkout
mount reported 9p. Synthetic databases were created on the native Linux filesystem, not inside the
Windows-mounted checkout.

A standalone uv 0.12.13 binary was downloaded from its official release into a task-specific Linux
directory. Sylvia's frozen lock installed 53 runtime dependencies into an isolated Linux virtual
environment. The project's Python requirement and locked dependencies were not changed.
[uv installation guidance](https://docs.astral.sh/uv/getting-started/installation/),
[project definition](../../pyproject.toml), [dependency lock](../../uv.lock).

The reproducible [owner probe](../../.scratch/astra-hardening-2026-09-14/probes/linux_owner_probe.py)
uses the real application classes and disposable synthetic data. It first attempts the unchanged
store import, then copies that one module into a temporary directory and makes its msvcrt import
conditional on Windows. All backup, migration and ownership checks remain active. No fake msvcrt
module, backup bypass or production-file patch was used.

| Probe | Observed result |
| --- | --- |
| Unchanged SQLite authority import | Fails at import msvcrt, before the store can load. |
| Isolated copy with conditional import | Loads successfully. |
| Complete owner-process module import | Loads with the locked Linux dependencies; no provider is started. |
| Existing cross-process lock | A second process is rejected while held and succeeds after release. |
| Fresh application owner | Opens a new base database. |
| Simple campaign with an empty Ruleset lock | Saves, then reopens with its expected display name. This is a backend record, not a playable campaign test. |
| Fresh independent Builder record | Rejected with backup_target_envelope_unsupported during schema preparation. |
| Existing backup API | Rejected with backup_target_envelope_unsupported. |
| Database after those failures | Reopens; integrity_check returns ok and schema version remains 3. |

The final probe exits successfully after recording those expected blockers. That does not mean
the blocked features passed. The first attempt also produced the full synthetic call chain:
create_builder_chat → _ensure_campaign_schema → _preserve_pre_migration_source → backup_database →
_validate_backup_envelope.

No desktop compilation, PyInstaller executable, AppImage, installed UI, real model request,
sign-in, migration success or cross-distribution run was verified. Rust/Cargo, the native C toolchain
and pkg-config were unavailable on the checked PATH; GTK development files were also absent.
I did not install system packages just to reach already-established application blockers.

The isolated dependency environment is under $HOME/sylvia-ah08-L7hAFJ inside Ubuntu.
The probe creates disposable /tmp/sylvia-ah08-owner-* directories. It does not read credentials or
personal campaigns. No shell profile, production application source or installed Windows candidate
was changed.

## The work the port actually needs

| Area | Current evidence | Smallest useful change |
| --- | --- | --- |
| Backend import | Unconditional msvcrt in SQLite authority. | Platform-specific import beside the platform implementation. |
| Owner startup | Development launch names .venv/Scripts/python.exe; packaged launch names sylvia-gm-owner.exe. | Choose platform paths while keeping the same supervised owner and authenticated pipes. |
| Backup, export and migration | Windows volume checks, file attributes, handles, flushes, replacement and cleanup. Backup blocks even fresh Builder schema preparation. | A focused Linux implementation of those operations, sharing the existing database/archive logic. |
| Desktop bundle | NSIS and ICO selected; sidecar build already understands target triples. | A Linux configuration, existing PNG icons and a Linux-built sidecar. |
| Release assembly | Windows artifact kinds, platform identity and disclosure text are built into the release helper. | A small Linux artifact description once a candidate exists; preserve the Windows path. |
| Desktop behavior | Multiple windows, file dialogs, media and provider discovery cross OS boundaries. | Exercise the actual Linux desktop paths and handle demonstrated differences. |

Source: [SQLite authority](../../src/sylvia_gm/infrastructure/sqlite_authority.py),
[archive publication](../../src/sylvia_gm/infrastructure/campaign_archive.py),
[owner launcher](../../src-tauri/src/lib.rs), [bundle configuration](../../src-tauri/tauri.conf.json),
[sidecar builder](../../src-tauri/scripts/build_owner_sidecar.py),
[release assembly](../../src/sylvia_gm/release_artifact.py).

This is a contained platform project by code ownership, but it is more than changing a build flag.
The launch/configuration edits are comparatively straightforward. Saved-file publication and
desktop verification are the uncertain parts. There is not enough implementation evidence for a
reliable day count yet; the next prototype should establish it.

## How I would handle saved data

Keep the existing SQLite backup API, archive format, package validation and one database owner.
Move operating-system file operations behind a small module used by backup and export. Do not
model Windows handle flags as if their Linux equivalents had identical guarantees.

A proposed Linux path would create an exclusively owned temporary destination, use the current
consistent SQLite copy, validate it, flush the file, replace the destination on the same filesystem,
and sync the destination directory. Linux rename provides atomic replacement, but file fsync alone
does not make the directory entry durable. These are separate operations.
[Linux rename documentation](https://man7.org/linux/man-pages/man2/rename.2.html),
[Linux fsync documentation](https://man7.org/linux/man-pages/man2/fsync.2.html).

The existing backup function already requires the caller to exclusively manage the admitted
destination directory throughout the operation; its extra handle restrictions provide additional
protection. Preserve that supported precondition. A Linux port does not need a new framework for
hostile concurrent manipulation of every destination, but it must retain source-alias checks,
safe temporary ownership and honest cleanup.

Three details need an explicit implementation design:

- Admit supported local filesystems deliberately. A Linux path beginning with a slash does not
  establish that it is local. The current Windows fixed-disk classification cannot simply become
  a root-path or device-number check.
- Use Linux file identity and non-following file operations appropriately. Do not replace
  handle-bound cleanup with unchecked deletion of whatever later occupies the same pathname.
- If replacement succeeds but the subsequent directory sync fails, report that durability is
  uncertain and retain the artifact. Do not claim the old destination is still present or proceed
  with a database migration as if the prerequisite backup had succeeded.

For the first experiment I would use local ext4 and selected synthetic data. That is a proposed
test scope, not an ext4-only public support decision. Network/synchronized live database storage
already falls outside the current [architecture contract](../architecture/alpha-architecture-contract.md).
Backups, exports and migration must retain their guarantees before Linux becomes an advertised
platform.

## Installation options

| Option | What a Linux user gets | Assessment for Sylvia |
| --- | --- | --- |
| AppImage | A downloadable application they make executable and run. | Best first broad-distribution experiment; still has a compatibility baseline and possible FUSE setup. |
| .deb | A normal package installation with desktop integration on compatible Debian-family systems. | Useful fallback or convenience package; not a universal Linux installer. |
| .rpm | Native installation for compatible RPM-family systems. | An additional packaging path to test if demand justifies it. |
| Flatpak | Installation and updates through a configured remote, with a shared runtime. | Worth considering later; introduces sandbox integration work for this application. |

Sources: [Tauri AppImage](https://v2.tauri.app/distribute/appimage/),
[Tauri Debian](https://v2.tauri.app/distribute/debian/),
[Tauri RPM](https://v2.tauri.app/distribute/rpm/),
[Flatpak concepts](https://docs.flatpak.org/en/latest/basic-concepts.html).

AppImage is not Ubuntu-only. Ubuntu can produce a package intended for other compatible
distributions. Tauri recommends building against the oldest baseline intended for support and
lists Ubuntu 22.04 and Debian 12 as examples with WebKitGTK 4.1. Its AppImage media-bundling support
currently favors an Ubuntu build environment. A build on the available Ubuntu 24.04 does not
establish that it runs on older hosts.
[Tauri AppImage limitations](https://v2.tauri.app/distribute/appimage/#limitations).

Build the Python sidecar on the same compatible Linux baseline. PyInstaller bundles Python but
does not bundle glibc or cross-compile a Windows executable into a Linux one. A compatible outer
desktop package cannot compensate for a sidecar requiring newer system libraries.
[PyInstaller manual](https://pyinstaller.org/en/stable/),
[Linux compatibility](https://pyinstaller.org/en/stable/usage.html#making-gnu-linux-apps-forward-compatible).

Start with manual package replacement for updates, matching the absence of automatic updates in
the current Alpha scope. App-menu integration for a standalone AppImage is a separate convenience.
A .deb can simplify that experience for its target systems without forcing every Linux user onto
Ubuntu. Some AppImages require FUSE compatibility packages; extraction is a documented fallback.
[AppImage desktop integration](https://docs.appimage.org/user-guide/run-appimages.html#integrating-appimages-into-the-desktop),
[AppImage FUSE guidance](https://docs.appimage.org/user-guide/troubleshooting/fuse.html).

I have not verified Wine or Proton as a Sylvia workaround. A browser edition would introduce a
different client/integration project. Neither is an evidence-backed shortcut to this native port.

## Checks that can change the recommendation

**Provider runtime discovery.** The current
[Codex connection](../../src/sylvia_gm/provider/_codex_rpc.py) looks on PATH, with an additional
Windows-specific discovery path. Linux app-menu launch must find the runtime too; a successful
terminal launch is insufficient because desktop processes do not necessarily receive shell
startup PATH additions. Test the currently supported provider options through the packaged app.
[Tauri's Linux PATH note](https://v2.tauri.app/distribute/debian/).

The existing Codex child environment already excludes LD_LIBRARY_PATH, which is useful because
PyInstaller modifies that variable. Preserve this behavior; it is not a newly found defect.
Flatpak would also need an explicit choice for finding or bundling the external runtime, since
host executables are not automatically available inside its sandbox.
[PyInstaller subprocess guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application),
[Flatpak filesystem access](https://docs.flatpak.org/en/latest/sandbox-permissions.html#filesystem-access).

**Sidecar startup.** The current PyInstaller one-file mode extracts native libraries into a temporary
directory. A noexec temporary filesystem can prevent startup. Test the current format first; a
bundled PyInstaller directory inside the application is an alternative if this proves significant.
[PyInstaller execution model](https://pyinstaller.org/en/stable/operating-mode.html).

**Window and media behavior.** Test extra windows, resizing, file dialogs, map drawing and representative
sound/animation on the intended Linux desktop. WSLg is useful for development but does not qualify
the ordinary desktops or their graphics drivers.
[Tauri Linux graphics guidance](https://v2.tauri.app/develop/debug/linux-graphics/).

There is one concrete behavior decision here. On native Wayland (the display system used by many
Linux desktops), this window API cannot reliably read or restore global screen coordinates.
Sylvia's [panel-window implementation](../../src-tauri/src/panel_windows.rs) saves outer_position
and restores set_position before showing a window. The locked Tao 0.35.3 Linux implementation
returns cached GTK coordinates rather than a capability error. A successful call therefore does
not prove the requested position was restored.
[Tao positioning contract](https://github.com/tauri-apps/tao/blob/tao-v0.35.3/src/window.rs#L706),
[GTK position documentation](https://docs.gtk.org/gtk3/method.Window.get_position.html).

I recommend restoring which panels were open and their usable sizes, while letting native Wayland
place them. Avoid saving synthetic coordinates as if they were real screen positions. This is a
specific proposed exception to exact placement restoration and needs Wan's decision. It does not
require dropping extra windows or their saved panel layout. Forcing X11/XWayland is an alternative
to test, but adds an environment dependency and still does not guarantee that the window manager
honors every placement request.
[GTK backend selection](https://docs.gtk.org/gtk3/running.html#environment-variables),
[GTK move semantics](https://docs.gtk.org/gtk3/method.Window.move.html).

The source does not support claiming that an unsupported Wayland move itself prevents Sylvia's
window from showing: Tauri's Result covers event dispatch, while the eventual GTK move returns
no capability result. Native testing should establish visibility, sizing and repeated restoration.
[Tauri runtime dispatch](https://github.com/tauri-apps/tauri/blob/tauri-runtime-wry-v2.11.4/crates/tauri-runtime-wry/src/lib.rs#L2343),
[Tao GTK requests](https://github.com/tauri-apps/tao/blob/tao-v0.35.3/src/platform_impl/linux/event_loop.rs#L301).

## Recommended next step and Alpha decision

Attempt a bounded prototype with these outcomes, in order:

1. The native backend creates a Builder session and campaign, saves and reopens them, and performs
   a verified backup/export and representative schema upgrade. Failures preserve the source.
2. The existing Tauri frontend launches that owner in a Linux build. The installed candidate can
   complete a small selected campaign journey, exercise provider/tool calls, stop/recover, use
   additional windows and reopen saved work.
3. Package it as an AppImage built on the chosen compatibility baseline, then try it on Ubuntu and
   a materially different distribution such as Fedora. Check desktop launch, installation friction
   and the actual session/window behavior, not only whether the executable starts.

These are proposed implementation outcomes, not work already selected or passed. Public Linux
support still needs Wan's decision and the corresponding product/architecture updates.

My present recommendation is to attempt that prototype before making Linux a launch requirement.
The reusable core and successful native dependency/ownership probes make it a reasonable investment.
The fresh-Builder migration failure makes a quick unqualified release a poor bet. Once backup,
migration and the packaged journey work, decide whether the remaining platform differences are
acceptable for Alpha and state the tested environments plainly.
