# Riichi Mahjong

Four seats: one player and three GM-controlled opponents. Start an `east_only` or
`east_south` match through the ordinary `start_game` tool. The engine supplies current
opaque action IDs; the player uses the table and the GM chooses for its active seat.
Forced passes advance automatically. Ordinary gameplay and OOC conversation preserve
the saved table.

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
and the reusable `piece_table` surface. No Mahjong dispatch exists in the core engine.
The canonical participant binds the human seat; clients cannot request another viewer.
This preserves a future four-human seam without adding networking.

The selected profile is Mahjong Soul-like four-player Riichi with three red fives.
It is not a claim of exact client parity. See the [implementation scope](../../proposals/riichi-mahjong.md)
for selected scoring, source confidence and remaining acceptance checks. The
[official ranked rules](https://mahjongsoul.com/news/46) determine multi-ron,
kan timing and settlement; open-hand nagashi eligibility has a documented secondary
source basis. The tournament manual describes a different competition configuration.

Tile labels and presentation are original text/CSS. No third-party game art, code,
characters or rules prose is included. The repository's licence and scope apply.
