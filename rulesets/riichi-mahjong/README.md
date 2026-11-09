# Riichi Mahjong

Four seats: one player and three NPC opponents. Start an `east_only` or
`east_south` match through the ordinary `start_game` tool. The engine supplies current
opaque action IDs; the player uses the table and each NPC chooses in its own private
context using the configured model. The GM retains campaign context and narrates
committed public results.
Forced passes advance automatically. Ordinary gameplay and OOC conversation preserve
the saved table.

The default library ships `riichi-mahjong-0.1.1.sylvia-gm-package` and The Lantern
Table Setting. The bundled **Riichi Quick Test** Plan includes the player **You**
and asks only for East-only or East-South. Select it during ordinary New Campaign
setup to reach a real dealt hand without Builder or character authoring.

The package declares tile faces, four discard ponds, called melds, concealed racks,
scores and round counters. Select a hand tile, then Discard; arrow keys move the
selection. Calls and wins appear when legal. Hover or focus provides descriptions.
The shared panel framework supports docking, resizing and pop-out windows.

Startup admits the new closure normally and preserves existing versions and user
edits. The original 0.1.0 archive remains retained in source. Existing campaigns
keep their exact package pins and original presentation; they are not rewritten.

Build an importable package from the repository root:

```powershell
python -m scripts.build_riichi_ruleset --archive riichi-mahjong.sylvia-gm-package
```

Import that archive through the ordinary package Library/Builder flow and select
Riichi Mahjong with the included original Setting, The Lantern Table. It uses the `player`
participant artifact with a name and
`control: player`. No separate adventuring system, network service or paid asset is required.

The package declares all Mahjong mechanics. Shared core additions are bounded value
operations, explicit public results, hidden random collections, private state roots,
the reusable `piece_table` surface and opt-in actor-owned decisions. No Mahjong
dispatch exists in the core engine.
The canonical participant binds the human seat; clients cannot request another viewer.
This preserves a future four-human seam without adding networking.

The selected profile is Mahjong Soul-like four-player Riichi with three red fives.
It is not a claim of exact client parity. See the [implementation scope](../../proposals/riichi-mahjong.md)
for selected scoring, source confidence and remaining acceptance checks. The
[official ranked rules](https://mahjongsoul.com/news/46) determine multi-ron,
kan timing and settlement; open-hand nagashi eligibility has a documented secondary
source basis. The tournament manual describes a different competition configuration.

Tile faces use original SVG/CSS artwork and system-font characters. No third-party game art, code,
characters or rules prose is included. The repository's licence and scope apply.
