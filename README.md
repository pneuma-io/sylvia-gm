# Sylvia GM

Sylvia GM is an AI Game Master for Tabletop RPGs.

Play through adventures with an AI GM, create your own worlds and campaigns, and choose the model
that runs them. Sylvia combines conversational storytelling with a game engine that handles
supported rules and keeps track of your campaign.
Rather than being another online platform, this is an Agent Harness for any compatible inference 
provider, with tools tailored to running TTRPGs, and an expandable framework to customize those
TTRPGs to your individual tastes.

## Play your way

- Describe what your character does and let Sylvia guide the story, play the supporting cast and resolve actions.
- Use the included SRD Lite rules and Severed Lands adventures, or build a different roleplaying experience.
- Create and refine rulesets, settings, campaign plans and characters in Builder.
- Save your campaign and return to its characters, history and progress.

## Choose your AI

Connect a supported hosted provider or an already-running local model server. Manage connections
and model choices in the app's Models settings. Hosted services may charge for usage; local models
depend on your hardware and their ability to follow instructions and use tools.

For ChatGPT account connections, Sylvia requires the supported
[Codex runtime](https://developers.openai.com/codex/cli/) to be installed separately. Available models
and account access depend on your provider; a connection option does not guarantee compatibility
with every account or model.

## Included content

SRD Lite is based on the D&D SRD 5.2.1, with 117 supported spells and a deliberately limited launch
scope. Consumables are outside this Lite release. Severed Lands supplies the included setting and
adventures.

Custom content uses three building blocks: Rulesets define mechanics, Campaign Settings define
the world, and Campaign Plans define an adventure. Builder helps you create and refine that content
for use in a compatible campaign.

## What's next

The next areas of development are:

- **Multiplayer** — shared adventures with other players.
- **SRD Full** — broader rules coverage beyond the Lite release.
- **User experience and interface improvements** — clearer controls, better gameplay panels and smoother everyday use.
- **Sylvia Foundry integration** — access to community-created content from within the app.
- **DM Assistant mode** — support for human Game Masters preparing and running their games.

These are future directions, not features included in the current Alpha. Priorities and release timing will be set as development progresses.

## Build from source

These instructions are for Windows source development. Playing an existing build does not require
the development toolchain.

Install these prerequisites:

- Windows 11 and [Git](https://git-scm.com/install/windows).
- [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.11 or newer, as required by [pyproject.toml](pyproject.toml).
- Rust with the MSVC toolchain, Microsoft C++ Build Tools with the **Desktop development with C++** workload, and a Windows SDK. Follow the [Tauri Windows prerequisites](https://v2.tauri.app/start/prerequisites/#windows) and [Windows SDK guidance](https://learn.microsoft.com/en-us/windows/apps/windows-sdk/).
- Microsoft Edge WebView2, described in the [Tauri WebView2 setup](https://v2.tauri.app/start/prerequisites/#webview2).
- The Tauri 2 CLI. Its [official installation command](https://v2.tauri.app/reference/cli/) is `cargo install tauri-cli --version "^2.0.0" --locked`.

Clone the repository, then run the development commands in PowerShell:

```powershell
git clone https://github.com/pneuma-io/sylvia-gm.git
cd sylvia-gm
uv sync --locked --all-extras
uv run --locked --all-extras python src-tauri/scripts/build_owner_sidecar.py
cd src-tauri
cargo tauri dev
```

Keep the checked-in lockfiles. The `--locked` Python commands reject an outdated lockfile instead
of rewriting it. The `sylvia-gm` Python command provides `--help` and `--version`; launch the desktop
with the Tauri command above.

### Developer tests

From the repository root, after syncing the Python dependencies:

```powershell
$env:PYTHONDONTWRITEBYTECODE=1
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
.venv/Scripts/python.exe -m pytest -s -p pytest_asyncio.plugin -p no:cacheprovider
```

Node and browser checks also need a supported [Node.js LTS](https://nodejs.org/en/download) with
npm. The [browser-test lockfile](tests/package-lock.json) requires Node 20 or newer and pins
Playwright 1.62.1. Install those dependencies from the repository root with `npm --prefix tests ci`.
Windows browser checks use Microsoft Edge; see the [Playwright browser setup](https://playwright.dev/docs/browsers)
for browser installation details. These are test prerequisites, separate from running Sylvia.

See the [development process](docs/operations/quality-preserving-development-process.md) for
focused verification and the [architecture contract](docs/architecture/alpha-architecture-contract.md)
for implementation details.

## Documentation

- [Documentation index](docs/INDEX.md)
- [Builder content reference](docs/reference/content-formats.md)
- [Included content, compatibility and verification status](docs/launch-compatibility.md)
- [Alpha readiness and remaining release work](docs/plans/alpha-rebuild-readiness-plan.md)
- [Website](https://pneuma-io.dev/sylvia-gm/)

Sylvia GM uses [PolyForm Noncommercial 1.0.0](LICENSE) with
[Sylvia Additional Permissions](SYLVIA-ADDITIONAL-PERMISSIONS.md); see
[licensing scope and preserved grants](LICENSING-STATUS.md). Included content retains its
[SRD 5.2.1 attribution](rulesets/srd-lite/ATTRIBUTION.md) and
[Severed Lands attribution](campaign-settings/severed-lands/ATTRIBUTION.md).
