# Sylvia GM Alpha Completion Plan

> **Status:** Active planning and handoff authority
>
> **Updated:** 2026-11-13

This is the repository status surface synchronized with Dot Desk (formerly Project Desk).
The latest owner-selected work item determines scope. The product contract owns requirements,
the architecture contract owns mechanisms, and the development process owns working method.
Historical checkpoints select no new work.

The existing PR #6 current-work paragraphs are retained by that branch and must remain here when
the branches meet. Their desktop/native, merge and release qualifications continue to apply.

## 1. Current work

**18 November 2026: Image Hook integration with current main.** PR #34 retains the complete
selected-style Severed Lands catalogue: 540 bundles and 1,620 PNGs, covering all 330 inherited
creatures. Main's command-router and GM-turn refactors now carry the image handlers. The 90
beast records are unchanged in content; their artwork bindings also recognize Lite 0.3.24's
exact runtime hash while preserving older bindings. Ready-party rebuilds retain the starter art.
Affected offline qualification is in progress using the current boat.dev procedure. Real Sylvia
generation and native/installed acceptance remain on Daniel's hold. Siren/TD playable integration
is deferred separately. Earlier Image Hook checkpoints below are historical evidence; the current
rulebook controls the merge, report and cleanup order.

**14 November 2026: character chat, No Ruleset Plans and browser windows.** Work item
`f68c1a9b-ad17-4728-a8ef-854e99e804e5` combines the delivered character-chat, rule-less
play and floating-panel changes, including the later structured No Ruleset Campaign
Plan delta. No Ruleset Plans retain authored world entities, objectives, secrets and
Plan Progress through the ordinary owner and persistence paths. Character chat stays
separate from campaigns; browser windows retain the native detached-panel path.
Focused source checks and a real-WebUI journey with a synthetic provider qualify this
PR. Independent review, acceptance and merge remain with Fluff; installed/native-input,
genuine-provider and release acceptance are separate and unselected. Artwork PR #34,
SylviaBench, Trigger Discipline and Media work remain outside this integration.

**13 November 2026: GM tool surface (W8 stage 1).** The owner selected showing the GM only
the SRD Lite tools the current campaign can use, from main `9090dd8`. Presentation-only
exposure conditions and casting-tool spell scopes are new in SRD Lite `0.3.24`; engine
admission is unchanged. The starter Cleric's first request falls from 154 tools and 420,975
tool bytes to 93 and 263,655. Per-spell casting inputs (W8 stage 2) and entry-point
consolidation (stage 3) remain unselected. Real-provider and benchmark checks are separate.

**12 November 2026: casting contracts and refusals.** The owner selected the casting,
refusal and Ruleset-guide follow-up in its own isolated checkout from main `ecfb8cf`.
PR33 and PR36 are merged; their caster filling, spell enums/normalization, memory,
stub filtering, complete common-input feedback, somatic default and self-touch are
retained. This follow-up adds actionable other-creature touch/distance refusals and
early invalid-Steed feedback, corrects the guide, and versions SRD Lite as `0.3.23`.
Real-owner regressions and a schema-minimal MCP sweep belong to the local runner.
Exact offline Evidence-script results and checked source/package identities accompany
the PR. Independent offline review remains required before merge. Installed/native,
real-provider, benchmark comparison and release acceptance are separate and unselected.
The older PR33/36 status rows below are historical and superseded by this paragraph.

**12 November 2026: Sylvia's own copy of Claude Code.** The owner selected a PR for the
version 0.5 requirement on Dot Desk item `749794ae-d27b-458e-b0d1-8578d66e9a1b`: a dedicated,
unmodified Claude Code installation, profile and sign-in provided by Sylvia setup. Settings now
downloads Anthropic's published binary into the owner data directory, checks its checksum and
Windows code signature, and runs it with its own profile. The earlier executable selection and
use of an existing sign-in are removed. Local installer, real-binary, runtime and Settings checks
qualify the source change; packaged desktop acceptance, release and Anthropic's terms for
products remain separate. The ChatGPT connection still needs the separately installed Codex
runtime. [Setup, terms and evidence](../research/claude-dedicated-instance-2026-11-12.md).

**11 November 2026: user-owned Claude runtime.** The owner selected a new PR for the existing
Dot Desk item `a420042f-c625-4f6e-aaa6-942519eaaa44`, including the future SylviaBench use of
his own subscription. The implementation adds native executable/authentication controls, a separate
provider and a tool handoff that retains Sylvia's deterministic authority. Opus 5.5 implemented
the scoped Accounts presentation. Local synthetic transport, installed-binary protocol and
existing-session checks qualify the source change. After the subscription allowance reset, a bounded
live Sonnet 5.5 check completed one tool operation and fresh-process continuation successfully. The
connection uses the current shared command router and preserves desktop-only native sign-in.
Benchmark comparisons, packaged desktop acceptance and release remain separate; the unmerged
benchmark framework is not imported here. [Setup and evidence](../research/claude-native-runtime-2026-11-11.md).

### Image Hook checkpoints before integration

**12 November 2026: selected Severed Lands art direction.** Daniel selected K, dark heroic
fantasy painting with anime-influenced character drawing. The shared catalogue now declares
that Setting default and its immutable rendering reference. GM and independently launched
Builder requests capture it through the existing image lifecycle; the style is retained across
reference/portrait stages and restart, while explicit user style instructions take precedence.
Actual Lite and independent-setting checks cover selection, replay-safe Review progression,
canonical references and map-reference ordering. Live style fidelity remains unqualified.
Selected K coverage is now 15 bundles and 45 active PNGs. Anchor Guardian, Stern, Linden, Marla,
Henna and Anvil add the latest six complete reference/portrait sets. The earlier nine K bundles
are unchanged. The latest assets retain concealed personal details and the Guardian's nonhuman
stone/organic design; its existing inherited-form binding also resolves the new art. Exact source
aliases and narrative/mechanics prefixes are retained; earlier editions and 45 prior PNGs remain
addressable for saved references. The other 185 bundles retain their prior art. The
200-bundle/600-active-PNG catalogue
is a technical coverage checkpoint, not completion of K. Wider K production and the remaining
catalogue are open. Live/in-app checks and separately owned Siren playable conversion remain
deferred; this increment does not select merge or release.

Daniel's subsequent portrait comparison reopened visual fidelity. The prepared K-labelled sets
pass technical source/resource checks, but their drawing, texture and colour treatment are not
accepted as a match to his supplied reference. Catalogue expansion now follows one controlled
Marla portrait correction with her design fixed and the exact comparison image supplied separately
for style. Daniel clarified that the loss of anime influence is the concern and requested a more
comprehensive guide. The K guide now specifies anime facial/eye/hair construction, deliberate
shadow shapes, colour separation and selective materials, with the exact benchmark supplied at
every stage. New requests use this versioned brief; retained earlier styles keep admitted flows
usable. A complete fixed-design Marla calibration is under review. Existing character artwork is
unchanged, and no calibration preview has been adopted into the catalogue.

**10 November 2026: GPT Images phase one (PL-10).** The owner selected generation/editing through
the managed ChatGPT subscription connection, Off/Review/Auto permissions, saved full-body and
multi-view character references, shared adjustable portrait framing and map-data-guided underlays.
GM and Builder receive autonomous art discovery and request tools. Separate scene illustrations
and ambient backgrounds remain deferred. PR #34 contains the hook, persistence and shared-display
implementation. The owner clarified that phase one must also cover the overall ordinary user
experience: prepared art throughout the shipped character/enemy catalogue, discoverable creation
and editing, imported-card artwork, Builder interview integration and reuse throughout later play.
The initial ready-to-merge claim does not cover that outcome and is withdrawn. Hold merge while
these gaps are completed. The current implementation adds portable, hash-checked package PNG
references and actively restores selected imported card portraits in Builder, including their use
as later generation references. Local portrait reuse also works with generation Off. Focused checks cover fresh-owner restore,
crop persistence, corrupt-asset rollback, common PNG inputs and card reference reuse. The next
increment adds a complete character artwork request, Review/Auto stage progression, Party and
character Artwork actions, and automatic image inclusion in Setting/Plan publication. Source
checks cover staged creation, failure/cancellation, the WebUI, GM export and fresh-recipient
package restoration. Follow-up work adds explicit partial-art records, saved-source links for
later adopted stages, library/campaign materialization and portrait-only reuse with generation Off.
Saving a character now prepares its artwork workflow under Review/Auto; conversation-model image
tool calls are optional. Focused source checks cover those handoffs and campaign-copy isolation.
All twelve SRD starters now have complete prepared art: Mara Flint, Neri Vale, Oren Reed,
Sela Moss, Bram Alder, Iri Swift, Tess Rowan, Kest Wren, Pip Venn, Luma Ash, Veya Quill and
Eli Penn each have a generated full-body image, multi-view sheet and portrait. Their starter files retain the shared visual
records; the installed catalogue also supplies these assets to retained Plan editions. Real-PNG
checks cover crop, reopen and export/recovery without the catalogue. Reopening resolves retained
images for the same campaign, character, purpose and hash before looking for original assets.
Anchor Sentinel also has a prepared nonhumanoid bundle and source profile links. Known entities
reuse it through their exact Setting source reference during normal status refresh; names alone
do not select art. The Cartographer and flesh-fused Anchor Guardian now have complete bundles
and source profile links too. The Cartographer's hood conceals their entire face in all three
assets. Tessa, Miri, Captain Harlan, Auntie Salt, Fume and Saffron also have complete bundles.
Their original biographies and mechanics are preserved, including Miri's concealed ring and
Fume's narrative-only supplies under Lite. Original and artwork-annotated source editions retain
exact catalogue bindings.
Rift Death Hound, Rift Basilisk, Rift Darkmantle, Rift Cube, Rift Ankheg and Rift Behir now
also have full-body references, multiview sheets and portraits. Original source text is unchanged.
The hound retains both heads, and faceless creatures use their full silhouette. The Basilisk
has a saved tighter crop; all six crops were inspected through the actual portrait renderer,
including dialogue circles and map-sized tokens. Incorrect generated variants were rejected
before adoption. These renders are source checks, not installed or live-provider acceptance.
Stern, Linden, Marla, Henna, Anvil and the Quartermaster also have complete prepared bundles.
Their existing profiles and pronouns are unchanged; unspecified appearances are editable defaults.
Three initial portraits with painted circular cutouts were corrected before adoption. All six
selected square images were inspected in dialogue circles and map-sized crops using the actual
portrait module/CSS. Runtime portrait instructions now require continuous square backgrounds
and explicitly exclude painted masks, leaving the circular crop to the UI. The mixed enemy/NPC
journey also verifies the Basilisk's saved default crop,
independent instance adjustments and Miri's exact source-bound artwork despite a different display
name, followed by offline campaign export/recovery without the shared library. The final composed
case passed in 70.71 seconds; two direct source-resolution checks also passed. Its earlier
no-art assumption for Miri and a missing Setting scope in the updated fixture were corrected.
Pike, Old Crane, Iro, Rigg, Kit and Lorn now have complete prepared bundles too. Their original
profiles and the preceding 33 bundles are unchanged. Kit retains their authored age of twelve;
Crane's unknown origin remains unknown. All six saved crops were inspected through the actual
portrait renderer in square, dialogue-circle and map-sized forms.
Two focused checks passed in 122.95 seconds for all 117 real images/resources/source aliases
and all 218 authored subjects/246 current and retained source editions. Earlier owner, frontend
and live acceptance layers were not replayed for this artwork-only addition.
The other NPCs, companions and remaining enemies still need artwork.
Thread, Hook and Needle now each have a separate full-body reference, multiview sheet and portrait.
Their shared Lace Brothers profile retains its original text and adds all three editable visual
defaults together. Each binding uses the exact shared file hash plus the individual subject, so
an unnamed group reference remains ambiguous. The prior fifty bundles and their bindings are
unchanged. Actual square, dialogue-circle and map-token crops were inspected for all three.
The Lace Brothers increment brought the catalogue to 53 complete bundles. Its nine-image/resource check and
the 218-subject/249-edition roster check pass. The group transfer's initial full-record assertion
found expected recipient-local operation IDs after recovery; image hashes/paths, appearance and
crop were unchanged. The corrected case passed in 41.66 seconds, checking all portable fields
and exact image bytes with no catalogue readers or image requests. Touched Ruff, docs-index and
diff checks pass. Completed frontend/native matrices were not repeated.
Madame Thistle, Grim and Shrike now also have complete full-body, multiview and portrait bundles.
Their original profile prefixes and the preceding 53 bundles/bindings are unchanged. All nine new
PNG bytes, hashes/dimensions, installer resources and six current/retained source editions pass
offline verification, including refusal outside the selected Setting and lookup from actual
current Lite Setting payloads. All three square/dialogue/map-token crops were inspected. The
authored roster check passed in 2.63 seconds for 218 subjects/252 source editions. This data-only
addition reuses earlier lifecycle/owner/frontend qualifications; no completed matrix was replayed.
Maw, Tallow and Drum now each have a full-body reference, multiview sheet and portrait, bringing
the shared catalogue to 59 complete bundles. Their original profile prefixes and the preceding
56 bundles/bindings are unchanged. All nine exact PNGs, hashes/dimensions, external installer
resources and six current/retained source editions pass offline verification, including Setting
scope and lookup from actual current Lite payloads. Their square/dialogue/map-token crops were
inspected through the actual portrait module and CSS. The authored roster check passed in
3.05 seconds for 218 subjects/255 source editions. Unspecified appearances remain editable
defaults; Maw and Tallow's unknown origins and secrets remain unknown. Earlier completed
owner/frontend/native matrices were not replayed for this artwork-only increment.
Brother Ash, Mother Wick and Old Finch now also have full-body references, multiview sheets and
portraits, bringing the catalogue to 62 complete bundles. Ash retains his authored age, battered
armor and closed ledger; Wick retains her small stature; Finch remains seated in his reference
views to respect his limited mobility. Other appearance choices are editable defaults and their
home worlds remain unknown. The nine exact PNGs, external resources and six current/retained
source editions pass offline verification against actual current Lite payloads and Setting scope.
Their original profile prefixes and the previous 59 bundles/bindings are unchanged. Actual
square/dialogue/map-token crops passed visual review. The authored roster check passed in
2.61 seconds for 218 subjects/258 source editions; completed owner/frontend/native matrices
were not replayed.
Gavin Thatch, Dame Maris and Soot now have complete full-body, multiview and portrait bundles,
bringing the catalogue to 65 complete bundles. Their workwear and bearing follow the authored
profiles; unspecified appearance details remain editable defaults. All nine exact PNGs, external
resources and six current/retained source editions pass offline verification, including actual
current Lite payload lookup and Setting scope. Original profile prefixes and the previous 62
bundles/bindings are unchanged. Their square/dialogue/map-token crops passed visual review.
The authored roster check passed in 2.80 seconds for 218 subjects/261 source editions.
Completed owner/frontend/native matrices were not replayed for this artwork-only increment.
Thorn, Wren and Wick the chandler now have complete full-body, multiview and portrait bundles,
bringing the catalogue to 68 complete bundles. Thorn's they/them pronouns remain unchanged and
the chandler keeps a distinct appearance from Mother Wick. Saved individual crop settings tighten
the generated upper-body portraits for consistent face framing without altering PNG bytes.
Actual square/dialogue/map-token crops passed visual review. All nine exact PNGs, external
resources and six current/retained source editions pass offline verification against actual current
Lite payloads and Setting scope. Original profile prefixes and the previous 65 bundles/bindings
are unchanged. The authored roster check passed in 2.71 seconds for 218 subjects/264 source editions.
Completed owner/frontend/native matrices were not replayed.
Kale, Hennick and Lace the tailor now have complete full-body, multiview and portrait bundles,
bringing the catalogue to 71 complete bundles. Their market roles and personalities remain
authoritative; unspecified appearance details are editable defaults. Lace stays unrelated to
the Lace Brothers. All nine exact PNGs, external resources and six current/retained source
editions pass offline verification, including actual current Lite payload lookup and Setting
scope. Original profile prefixes and the previous 68 bundles/bindings are unchanged. Actual
square/dialogue/map-token crops passed visual review. The authored roster check passed in
2.68 seconds for 218 subjects/267 source editions. Completed owner/frontend/native matrices
were not replayed for this artwork-only increment.
Old Vesper, Corin and Davith now have complete full-body, multiview and portrait bundles,
bringing the catalogue to 74 complete bundles. The innkeeper stays distinct from the other
Vespers, and Davith's unmarked guard appearance does not disclose his private loyalties.
Unspecified appearance details remain editable defaults. All nine exact PNGs, external resources
and six current/retained source editions pass offline verification, including actual current Lite
payload lookup and Setting scope. Original profile prefixes and the previous 71 bundles/bindings
are unchanged. Actual square/dialogue/map-token crops passed visual review. The authored roster
check passed in 2.61 seconds for 218 subjects/270 source editions. Completed owner/frontend/native
matrices were not replayed for this artwork-only increment.
Lady Sera Kael, Lady Maren Trask and Lord Harren Dorn now have complete full-body, multiview and
portrait bundles, bringing the catalogue to 77 complete bundles. Their public council and merchant
roles guide the artwork; private motives and plot secrets stay unshown. Unspecified appearance
details remain editable defaults. All nine exact PNGs, external resources and six current/retained
source editions pass offline verification, including actual current Lite payload lookup and Setting
scope. Original profile prefixes and the previous 74 bundles/bindings are unchanged. Actual
square/dialogue/map-token crops passed visual review. The authored roster check passed in
2.70 seconds for 218 subjects/273 source editions. Completed owner/frontend/native matrices
were not replayed for this artwork-only increment.
Lord Silas Merrick, Lord Aldric Voss and Castellan Voss now have complete full-body, multiview and
portrait bundles, bringing the catalogue to 80 complete bundles. All five Thornwall councillors
now have prepared bundles. The unrelated Voss characters stay distinct; the Castellan's right-hand
gauntlet, left-arm shield and sheathed sword remain consistent through his reference views, with
no visual disclosure of private illness. All nine exact PNGs, external resources and six current/
retained source editions pass offline verification against actual current Lite payloads and Setting
scope. Original profile prefixes and the previous 77 bundles/bindings are unchanged. Actual square/
dialogue/map-token crops passed visual review. The authored roster check passed in 3.53 seconds for
218 subjects/276 source editions. Completed owner/frontend/native matrices were not replayed.
Iron Mother Hest, Joss and Kell now have complete full-body, multiview and portrait bundles,
bringing the catalogue to 83 complete bundles. Hest retains Dwarf proportions, Joss's they/them
pronouns remain unchanged and Kell retains her authored age. Saved crop adjustments for Hest and
Joss align face framing without changing PNG bytes. Actual square/dialogue/map-token crops passed
visual review. All nine exact PNGs, external resources and six current/retained source editions
pass offline verification against actual current Lite payloads and Setting scope. Original profile
prefixes and the previous 80 bundles/bindings are unchanged. The authored roster check passed in
2.77 seconds for 218 subjects/279 source editions. Completed owner/frontend/native matrices
were not replayed for this artwork-only increment.
Councillor Merrow, Deacon Ash and Doctor Cipher now have complete full-body, multiview and portrait
bundles, bringing the catalogue to 86 complete bundles. Their original profile prefixes and the
previous 83 bundles/bindings are unchanged. Unspecified appearances remain editable defaults;
Merrow's home world stays unknown, Ash's sheathed sword stays on his left hip through the
turnaround, and Cipher retains her personable scholarly bearing. Actual square/dialogue/map-token
crops passed visual review. All nine exact PNGs, external resources and six current/retained
source editions pass offline verification against actual current Lite payloads and Setting scope.
The authored roster check passed in 2.68 seconds for 218 subjects/282 source editions. Completed
owner/frontend/native matrices were not replayed for this artwork-only increment.
Father Glass, The Mirror and Gareth now have complete full-body, multiview and portrait bundles,
bringing the catalogue to 89 complete bundles. Glass retains his ordinary Human identity, The
Mirror retains violet-flecked natural eyes without added mutations, and Gareth's sword and pouch
stay on their respective anatomical sides through the turnaround. Unspecified appearances remain
editable defaults. Original profile prefixes and the previous 86 bundles/bindings are unchanged.
Actual square/dialogue/map-token crops passed visual review. All nine exact PNGs, external resources
and six current/retained source editions pass offline verification against actual current Lite
payloads and Setting scope. The authored roster check passed in 2.53 seconds for 218 subjects/285
source editions. Completed owner/frontend/native matrices were not replayed for this artwork-only
increment.
Cassian, the Prophet of Four Corners and Athenaeum librarian Vesper now have complete full-body,
multiview and portrait bundles, bringing the catalogue to 92 complete bundles. Cassian uses
adapted working clothes without recovered original medical equipment, and the Prophet retains the
Hollowed source's Speaker-stage violet eyes and still listening bearing. Librarian Vesper stays
separate from Siren's Vesper and Old Vesper: a bounded resolver check returns each source's own
portrait even with the same requested name and refuses mismatched source paths. That check reuses
Siren's existing admitted descriptor; playable conversion and party/Builder qualification remain
pending.
Unspecified appearances remain editable defaults. Original profile prefixes and the previous 89
bundles/bindings are unchanged. Actual square/dialogue/map-token crops passed visual review.
All nine exact PNGs, external resources and six current/retained source editions pass offline
verification against actual current Lite payloads and Setting scope. The authored roster check
passed in 2.61 seconds for 218 subjects/288 source editions. Completed owner/frontend/native
matrices were not replayed for this artwork-only increment.
Briar, Kestrel and Guildmaster Vance now have complete full-body, multiview and portrait bundles,
bringing the catalogue to 95 complete bundles. Briar's foraging bag and braid remain consistent
through the turnaround; Kestrel retains her controlled theatrical bearing and Vance his polished
merchant's bearing. Unspecified appearances remain editable defaults. Original profile prefixes
and the previous 92 bundles/bindings are unchanged. Actual square/dialogue/map-token crops passed
visual review. All nine exact PNGs, external resources and six current/retained source editions
pass offline verification against actual current Lite payloads and Setting scope. The authored
roster check passed in 6.66 seconds for 218 subjects/291 source editions. Completed owner/frontend/
native matrices were not replayed for this artwork-only increment.
Palla, Orin Thatch and Rook now have complete full-body, multiview and portrait bundles, bringing
the catalogue to 98 complete bundles. Palla's apron ties and silver hair streak, Orin's satchel
placement, and each character's face and clothing remain consistent through the references.
Rook's home world remains unknown; unspecified appearances stay editable defaults. Original
profile prefixes and the previous 95 bundles/bindings are unchanged. Actual square/dialogue/
map-token crops passed visual review. All nine exact PNGs, external resources and six current/
retained source editions pass offline verification against actual current Lite payloads and
Setting scope. The authored roster check passed in 12.30 seconds for 218 subjects/294 source
editions. Completed owner/frontend/native matrices were not replayed for this artwork-only
increment. The broader catalogue, everyday journeys and deferred acceptance remain unfinished.
Salt-Ash, Reef and Quill now have complete full-body, multiview and portrait bundles, bringing
the catalogue to 101 complete bundles. Salt-Ash retains Speaker-stage violet eyes; Reef retains
her faction's reptilian identity, one complete tail per view and consistent knife/pouch sides;
Quill retains her locally born second-generation identity. Unspecified features and clothes stay
editable defaults. Reef's saved crop aligns her face framing without changing PNG bytes. Actual
square/dialogue/map-token crops passed visual review. Original profile prefixes and the previous
98 bundles/bindings are unchanged. All nine exact PNGs, external resources and six current/retained
source editions pass offline verification against actual current Lite payloads and Setting scope.
The authored roster check passed in 6.76 seconds for 218 subjects/297 source editions. Completed
owner/frontend/native matrices were not replayed for this artwork-only increment. The broader
catalogue, everyday journeys and deferred acceptance remain unfinished.
At 29042e4, Rift Doppelganger, Dust, Earth, Ettercap, Ettin and Fire gained full-body, multiview
and portrait bundles, bringing the catalogue to 140 complete bundles. Doppelganger has an editable
representative appearance, without declaring a canonical true form or impersonation. Dust, Earth
and Fire retain their distinct faceless forms; Ettercap retains its selected four-eye arrangement.
Both Ettin heads remain visible in the revised portrait's dialogue circle, with source equipment
preserved in its body/sheet. The initial portrait and framing repair remain recorded. Unspecified
details remain editable visual defaults. Original profile prefixes and the previous 134 bundles
and bindings are unchanged. Actual square/dialogue/map-token crops passed visual review. All 18
exact PNGs, external resources and twelve current/retained authored source editions pass offline
verification against actual current Lite payloads and Setting scope. The authored roster check
passed in 3.09 seconds for 218 subjects/336 source editions.
At c96fb18, Rift Flameskull, Forge, Fungus, Violet Fungus, Ghast and Ghost gained full-body,
multiview and portrait bundles, bringing the catalogue to 146 bundles/438 PNGs. Forge's selected
body framing preserves its complete flame crown and hammer. Violet Fungus's selected sheet
repair preserves five caps in each view; both original prompts and corrective edits remain
recorded. Faceless fungi keep their complete silhouettes in portrait/token crops. Ghast and
Ghost retain anonymous editable appearances without invented life histories. All six actual
square/dialogue/map-token crops passed visual review. All eighteen exact assets/resources and
twelve current/retained authored editions pass offline verification against current Lite Setting
payloads, with the prior 140 bundles and original profile prefixes preserved. The authored roster
check passed in 10.79 seconds for 218 subjects/342 editions. This is artwork preparation only;
broader workflows and native/live acceptance remain unfinished.
At b6fd28b, Rift Cloud, Fire, Frost, Hill, Stone and Storm Giants gained full-body, multiview and
portrait bundles, bringing the catalogue to 152 bundles/456 PNGs. Each retains its authored
equipment and Rift traits. The selected Frost sheet framing keeps the full axe visible; the
selected Stone sheet inset shows one continuous club. Original and corrective prompts remain
recorded. Saved crop metadata aligns the six portraits without altering their PNG bytes; actual
square/dialogue/map-token crops passed visual review. All eighteen exact assets/resources and
twelve current/retained authored editions pass actual Lite Setting lookup, with prior 146
bundles/bindings and original profile prefixes preserved. The authored roster passed in 9.27
seconds for 218 subjects/348 editions. All 82 authored NPCs and 47 authored monsters are prepared;
89 authored monsters and the wider catalogue remain unfinished. No completed owner/frontend/native
matrix was replayed.
At 8548f71, Rift Ghoul, Gorgon, Grick, Griffon, Harpy and Hippogriff gained full-body, multiview
and portrait bundles, bringing the catalogue to 158 bundles/474 PNGs. The selected Ghoul body
correction removes unsupported horns/wings/tail; the selected Gorgon portrait framing retains
both complete horns in the dialogue circle. Harpy's saved crop aligns its face without changing
the portrait PNG. Grick retains four tentacles; Griffon retains lion hind paws/tail and Hippogriff
horse hind hooves/tail. Actual square/dialogue/map-token crops passed visual review. All eighteen
exact assets/resources and twelve current/retained authored editions pass actual Lite Setting
lookup, with prior 152 bundles/bindings and original profile prefixes preserved. The authored
roster passed in 9.16 seconds for 218 subjects/354 editions. All 82 authored NPCs and 53 authored
monsters were prepared; 83 authored monsters and wider catalogue/workflow acceptance remained open.
At f9c0886, Rift Hydra, Ice, Jelly, Kraken, Lamia and Lich received complete full-body, multiview
and portrait bundles, bringing the catalogue to 164 bundles/492 PNGs. Hydra retains five baseline heads;
Kraken retains ten tentacles in its complete references. Ice remains a faceless active shard,
Jelly the authored violet drifting mass, and Lich an anonymous partly remembered spellcaster.
Actual square/dialogue/map-token crops passed visual review, including all five Hydra heads.
Lamia and Lich use aligned saved framing without changing portrait PNG bytes. All eighteen
exact assets/resources and twelve current/retained authored editions pass actual Lite Setting
lookup, preserving prior 158 bundles/bindings and original profile prefixes. The authored roster
passed in 3.26 seconds for 218 subjects/360 editions. All 82 authored NPCs and 59 authored monsters
were prepared; 77 authored monsters and wider catalogue/workflow acceptance remained unfinished.
Anchor Sentinel's existing older catalogue ID still resolves both current/retained editions;
its artwork was reused without a duplicate bundle. No completed owner/frontend/native matrix
was replayed.
At 9338f6f, Rift Magma, Medusa, Merrow, Mimic, Minotaur, Mud, Mummy, Mummy Lord, Ogre, Oni,
Ooze and Owlbear received complete full-body, multiview and portrait bundles. The catalogue reached
176 bundles/528 PNGs, including all 82 authored NPCs and 71 authored monsters. All twelve actual
square/dialogue/map-token crops passed visual review after three portrait framing corrections
for Medusa, Minotaur and Oni; the other saved crops retain the selected PNG bytes. Complete
faceless silhouettes, six serpent heads and both Minotaur horns remain visible in the circles.
All 36 assets/resources, 24 current/retained authored editions and actual current Lite Setting
lookup pass, preserving prior 164 bundles/bindings and original profile prefixes. The authored
roster passed in 5.01 seconds for all 218 subjects/372 editions. There were 65 authored monsters
left; wider catalogue and ordinary workflow acceptance remained unfinished. No completed
owner/frontend/native matrix was replayed.
At e6b7820, Rift Peryton, Phase Spider, Piercer, Pudding, Purple Worm, Raider, Remorhaz, Revenant,
Roc, Roper, Rust Monster and Salamander received complete full-body, multiview and portrait art.
The catalogue reached 188 bundles/564 PNGs, including all 82 authored NPCs and 83 authored monsters.
Remorhaz and Rust Monster body anatomy and Roc sheet margins were corrected. All twelve saved
crops and square/dialogue/map-token views passed visual review. All 36 assets/resources and 24
current/retained authored editions pass exact checks and actual Lite Setting lookup. Prior 176
bundles/bindings and original profile prefixes remain unchanged. The roster passed in 5.54 seconds
for all 218 subjects/384 editions.
There were 53 authored monsters left, with wider catalogue and ordinary workflows unfinished.
Phase Spider's material resting reference preserves its vapor without resolving the existing
Veil-lore/Ethereal-Jaunt trait wording. No mechanics or planar execution were qualified by art.
Rift Seeker, Shadow, Shrieker, Skeleton, Smoke, Specter, Spore, Stalker, Steam, the Elder
Riftbeast, Treant and Troll now have full-body, multiview and portrait sets. The catalogue totals
200 bundles/600 PNGs, including all 82 authored NPCs and 95 authored monsters. All twelve actual
square/dialogue/map-token crops were reviewed, including the faint outlines and faceless forms.
Shadow/Stalker portrait contrast and Treant crown margins were corrected before adoption.
All 36 selected assets/resources and 24 current/retained authored editions pass exact checks and
actual Lite Setting lookup; prior 188 bundles/bindings and original profile prefixes remain
unchanged. The roster passed in 7.88 seconds for all 218 subjects/396 editions.
There are 41 authored monsters left, with wider catalogue and ordinary workflows unfinished.
Stalker is an editable reference visualization, not a revealed true body or Invisibility change.
Shrieker's existing mile-lore/300-foot action wording remains unresolved by the fungus artwork.
At f85bbcb, all 82 authored NPCs across
80 current profile files
and 165 source editions resolved complete artwork, with all 246 NPC PNGs/resources and actual
Lite lookup verified. That unchanged preparation remains qualified. The wider monster, inherited
form, Plan-only and companion catalogue remains unfinished. Completed owner/frontend/native
matrices were not replayed. Broader everyday journeys and deferred acceptance remain unfinished.
Anchor Trap's existing stone-lore/rug-mechanics inconsistency remains unresolved; its artwork
depicts the activated stone mechanism only, with mechanics unchanged.
The owner selected a shared app artwork library: whole Setting/Plan exports omit unused premade
PNGs, while custom art stays portable and character/campaign exports carry used artwork. The
source now maps the catalogue and PNGs as external installer/portable resources, outside the
onefile sidecar. Campaign export retains used art before any artwork view has been opened.
The prepared PNG pool now totals 1,339,919,189 bytes across 600 images, beyond the content-package cap.
The current Setting omits those PNGs; its existing content-package limits remain unchanged.
Focused checks cover shared/custom publication, external resource inventory, portable ZIP assembly,
and exact campaign art transfer before opening an image view. Local-copy receipts also transfer
without creating an agent session, while provider requests retain their session requirement.
Standalone character/profile transfer now has a bounded `.sylvia-gm-character` format, exact
package checks, preview/confirm/cancel and owner/WebUI upload/download endpoints. It carries
used art, crop and imported originals through the existing libraries and image store, preserving
legacy v1 text. Focused checks cover an imported card's crop/edit/re-export/reference journey and
actual Lite exact-package restoration with generation Off. Library now offers Character File
import with preview, confirm/cancel and a saved-character export list. Setup can export the exact
selected Player Character, Companion or Universal card. Imports refresh the lists; refused imports
retain their preview, and stale exports preserve setup choices and refresh the source list.
Native chooser/save commands and browser upload/download wiring are implemented. Source WebUI
checks cover actual Lite package refusal/recovery, portable artwork/original cards and zero image
requests, plus desktop/mobile layouts. Compiled Rust checks cover verified handoff and safe names.
Owner startup now retires abandoned temporary preview copies while retaining import receipts and
the user's chosen files, so closing the app cannot permanently exhaust the four-preview limit.
The following ordinary-edit audit confirmed that renaming a saved character could propose fresh
art despite its complete references. Builder publication now uses the opened source target for
preparation, reuse and links, with retained profile-open receipts across restart. Explicit sources
do not fall back to an unrelated same-named target. Four local edit checks pass, including actual
Lite, an independent Ruleset, restart and Auto isolation; eighteen direct consumers also pass.
The unfinished-art copy audit also confirmed duplicate proposals when a saved source was edited
in another Builder chat. Source identity/hash now retains the original pending/failed workflow
and links later adopted results to the copy, without cross-chat action authority. Local copy
receipts no longer mask a provider failure. Four local checks cover pending/failed/partial art,
Auto without submission, permission refusal and deleted-chat recovery; twenty-one direct consumers
also pass, including actual Lite. Live and installed acceptance remain deferred.
The following source audit found 80 NPC files and 136 authored monster files; the Lace Brothers
file contains three distinct named siblings. Shared-source lookup now supports an explicit subject
inside an exact package/path/hash match and refuses ambiguous bindings. The owner clarified that
premade coverage includes the Severed Lands inherited versions of SRD creatures/forms. Those
inherited identities, prose-only Plan casts and remaining authored subjects still need artwork.
The inherited inventory now identifies 330 Severed Lands subjects (90 beasts and 240 other forms)
across the current and retained Lite sources. Exact file/JSON Pointer/hash bindings are scoped
to the selected Setting. Portrait lookup uses admitted metadata without parsing the large
definition files. Producer and owner composition checks cover these bindings and used-art
transfer with generation Off; these fixtures do not supply the missing production artwork.
At 04f9e5f, seventeen inherited forms reused their prepared Severed Lands renditions: Ankheg, Basilisk,
Darkmantle, Death Dog, Gelatinous Cube, Doppelganger, Dust Mephit, Earth Elemental, Ettercap, Ettin
and Fire Elemental, plus Air Elemental, Behir, Bulette, Cockatrice, Flesh Golem and Magmin. Flesh
Golem uses the prepared Anchor Guardian look; Magmin uses Rift Cinder. These are Setting-specific
artwork choices; mechanical payloads and runtime identities are unchanged. Exact Ruleset
file/hash/JSON Pointer plus Setting scope selects the existing complete bundle. Those six added
bindings pass actual current Lite byte/metadata lookup and wrong Setting/hash/package/pointer
refusal. All 140 visual records, prompts and crops and all earlier bindings are preserved; the
eighteen reused PNGs pass exact-byte/hash lookup. No images were created or copied, and external
resources remained unchanged. This qualifies artwork lookup only; inherited form execution remains
unqualified by this check.
At c96fb18, four further inherited records reused the prepared renditions: Azer Sentinel uses Rift
Forge; Violet Fungus, Ghast and Ghost use their matching Rift renditions. Actual current Lite
file bytes and admitted hash/size metadata resolve all four exact records; wrong Setting, hash,
package, pointer and changed bytes are refused. The previous 140 bundles remain unchanged. This
brings inherited coverage to 21 forms, without extra image copies or mechanical payload changes.
It qualifies artwork lookup, not inherited form execution. No missing Flameskull/Myconid entry
was invented, and the walking Rift Fungus was not bound to the distinct Shrieker Fungus.
At b6fd28b, six inherited Giant records reused matching Rift Giant bundles, bringing inherited
coverage to 27 forms. All six exact source records resolve through current Lite bytes and admitted
file metadata; wrong Setting/hash/package/pointer/changed-byte lookups are refused. All eighteen
reused PNGs pass runtime byte/hash lookup. The selected Cloud morningstar is a rigid spiked mace;
the inherited Thunderous Mace attack and all other mechanical payloads remain unchanged. These
Setting artwork choices do not qualify inherited form execution or change participant identities.
At 8548f71, six further inherited records reused Ghoul, Gorgon, Grick, Griffon, Harpy and Hippogriff art,
bringing inherited coverage to 33 forms. Exact current Lite bytes and admitted metadata resolve
all six; wrong Setting/hash/package/pointer/changed-byte lookups are refused. All eighteen reused
PNGs pass runtime byte/hash lookup. Authored Gorgon and Grick remain monstrosities; their inherited
records remain construct and aberration respectively. Compatible physical artwork is reused
without changing either source type. Harpy retains its authored club without adding an inherited
attack. Mechanical payloads and participant identities are unchanged; form execution remains
unqualified by these artwork checks.
At f9c0886, six further inherited records reused the prepared Hydra, Ice Mephit, Ochre Jelly,
Kraken, Lamia and Lich renditions, bringing inherited coverage to 39 forms. Exact Lite bytes and admitted
metadata resolve all six; wrong Setting/hash/package/pointer/changed-byte lookups are refused.
All eighteen reused PNGs pass runtime byte/hash lookup. Lamia retains authored monstrosity and
inherited fiend labels, and her authored dagger adds no inherited attack. The shard, floating
Jelly and partly remembered Lich are Setting-specific artwork choices; no movement, flight,
regeneration, combat payload or participant identity changes are introduced. These checks qualify
artwork lookup only, with inherited form execution and native/live acceptance still unqualified.
At 9338f6f, eleven inherited records reused the prepared Magma Mephit, Medusa, Merrow, Mimic,
Minotaur of Baphomet, Mummy, Mummy Lord, Ogre, Oni, Gray Ooze and Owlbear renditions, bringing
coverage to 50 forms. Exact current Lite bytes/admitted metadata, wrong scope/hash/package/
pointer/changed-byte refusals and all 33 reused runtime PNG byte/hash lookups pass. Oni retains
authored giant and inherited fiend labels. Authored visual equipment remains selected: the
Minotaur has a greataxe and Oni a glaive; their inherited attack records remain unchanged.
Mud Mephit is absent from the current inherited index and was not fabricated; the separate
Minotaur Skeleton was not bound to living Minotaur artwork. These are artwork lookup checks,
with no mechanical execution or equipped-loadout qualification and no extra image copies.
At e6b7820, eight inherited records reused Phase Spider, Black Pudding, Purple Worm, Remorhaz,
Roc, Roper, Rust Monster and Salamander artwork, bringing coverage to 58 forms. Current Lite
bytes/admitted metadata, wrong scope/hash/package/pointer/changed-byte refusals and all 24 reused
runtime PNG byte/hash checks pass. Roper retains authored monstrosity and inherited aberration
labels. Inherited attacks, including Salamander's empty admitted attack mapping, remain unchanged;
its authored spear image does not qualify combat execution. Peryton, Piercer, Raider and Revenant
receive authored art without fabricated inherited records. No extra image copies are introduced.
Nine new inherited records reuse Shadow, Shrieker Fungus, Skeleton, Specter, Invisible
Stalker, Steam Mephit, Tarrasque, Treant and Troll art, bringing coverage to 67 forms. Actual Lite
bytes/admitted metadata, wrong scope/hash/package/pointer/changed-byte refusals and all 27 reused
runtime PNG byte/hash checks pass. Stalker retains authored Medium and inherited Large sizes;
unscaled reference art does not qualify footprint, Invisibility or combat behavior. The Elder
Riftbeast's authored name is preserved. Seeker, Smoke and Spore receive authored art without
fabricated inherited forms. Bugbear Stalker, the separate skeleton variants and Troll Limb are
not bound to this batch's art. No attacks, mechanical source declarations or extra image copies change.
Other Settings do not inherit those renditions. The mixed NPC/creature journey selects one
Basilisk through its inherited form and another through its authored Rift profile, preserving
independent crops and offline used-art recovery. The remaining 263 inherited forms still need art.
At 3b7cc8a, the original five-form actual-image/resource/alias check passed in 84.91 seconds,
including refusal outside Severed Lands; the mixed authored/inherited/NPC offline journey passed
in 73.42 seconds. All 33 then-current visual bundles and previous bindings were preserved. Those
source checks were not replayed for the current six-binding data addition.
Review of all eighteen shipped/retained Plan narratives identified seventy-two prose-only named
subjects, including side-story cast and the Vigilant Engine. The production roster preserves
separate identities for same-named people in different Plans. Exact accepted-Plan ID, cited prose,
subject and Setting bindings now restore their prepared art through normal status and
used-art campaign export. Source checks use synthetic PNGs; production of these images remains
open, alongside the remaining Setting, inherited and selected TD art.
The subsequent reuse audit found that generalized Plan copies received new story IDs and lost
their prepared cast binding. Copies now preserve a separate artwork source ID through repeated
save-as and ordinary Plan transfer. Exact cited prose still controls reuse. The actual Lite
composition check covers two copies, Plan export/import, Builder-to-GM handoff and offline used-art
campaign recovery with generation Off; no new production images are supplied by those fixtures.
The TD integration audit at `9968614adc97eddebb5a97f2cfccb772ee889cf1` found compiled mechanics
and synthetic conformance characters. The owner selected clean Siren Protocol content for the
trailer and corrected that README's blanket private-content label. Legacy private campaign notes
remain excluded; test fixtures do not become premade release characters.
The selected Siren repository's current main at `c7a9587b94f8e5125ea5818a9f708ec32d0bab01`
contains eleven authored profiles and runtime build inputs. Its historical repository/profile names do
not make the clean Siren material private; source preparation follows the owner's correction.
The eleven locked originals have now been delivered locally. Independent checks confirm the
normalized master archive hash, exact internal image bytes/checksums and browser decoding of all
eleven at 1024×1536. Ten historical registry hashes differ from that master; the discrepancy is
retained without an invented cause. All eleven original sheets are now retained byte for byte in
the prepared catalogue, alongside separate full-body and square-portrait derivatives. Each complete
bundle records its original filename/hash, archive identity and historical registry discrepancy;
generated sheet writing does not change the clean profile's narrative authority. The shared library
contained fifty complete bundles after that increment. All eleven portraits were inspected through the actual square,
dialogue-circle and map-sized crop renderer. Fifty-one exact source bindings cover the selected
upstream profiles and the delivered Siren Protocol 0.2.0 package source. Raw and normalized hashes
are retained where they differ, and other Settings do not select this cast. The twelve selected
source files were compared directly; existing visual identity and runtime character identities are
preserved. The delivered package source is separate from TD PR #6's checked head
`882103eee8d511d08d31d9d0b4fe4d9d67b813f8`, which does not contain that Setting. Actual TD party
and Builder handoff remains open. No other work item's source was modified.
The active TD owner subsequently confirmed that the retained Siren 0.2.0 adapter is explicitly
`included_nonmechanical` / `NOT_COMPLETE`, with no participant artifacts or converted Character
File envelopes. Sixteen selected immutable source blobs were checked independently; the twelve
clean character files match the retained artwork source copy after line-ending normalization.
This verifies provenance without establishing a TD 0.1.29 playable composition. Real conversion
and its party/Builder qualification require explicit reconciliation of the separate Siren
composition scope. The existing `visuals` field can carry prepared art through the generic
Character File and party path while preserving source identities and campaign participant IDs.
No adapter build, TD checkout write, guest use or native/live check was performed for this handoff.
Two focused checks passed in 122.04 seconds: all 150 real PNGs/resources/source bindings and a
real Siren bundle exported/imported through fresh owner libraries without the shared image reader.
The transfer retains all three exact PNGs, including the original sheet, and the adjusted crop,
with no image adapter or request. Its Universal fixture qualifies image transfer, not a TD build.
The previous 39 bundles and their bindings are unchanged. Touched Ruff, docs-index and diff checks
pass; previous frontend/Rust and broader campaign matrices were not repeated for this data addition.
The originals' standard PNG background-color hint exposed an overly narrow image
validator. Bounded `bKGD` support now preserves original bytes while refusing malformed or misplaced
hints. The six minimal failure cases pass after the repair; 78 related source checks pass in
120.81 seconds, including saved-owner restoration/reopen and actual Lite image lifecycle. Strict
module typing and touched Ruff pass; all eleven originals pass the repaired validator. This is
source qualification only, with native/live acceptance still pending.
The authored-source producer now covers 218 subjects (82 individual NPCs and 136 monsters),
retaining 318 source editions across current content and Plan snapshots. Thread, Hook and Needle
remain distinct; V and Anchor Sentinel variants and the artwork-annotated profiles retain
their original editions. The
snapshot-only opposition guide is explicitly excluded as encounter guidance. A composed source
check verifies every declared binding through the runtime resolver; this supplies no new images.
The subsequent local handoff check found and fixed an execution dependency on a stopped source
chat, including admission of the next character reference stage. An active chat in the same
campaign can now generate/adopt an existing individual request or continue the whole character
workflow with its original provenance and stable stage identities. Replays from another active
chat reuse those stages; unrelated chats remain refused. Nine local handoff/workflow checks pass,
including exact reference order and export/recovery. The owner clarified that model delegation
applies to visual design and frontend work. Backend design and implementation continue here.
The original Opus handoff failed before launch (`thread_credential_required`). After the owner
selected Opus 5.5 and resumed following the app update, the direct handoff executed successfully
with Fast mode off. Its transfer frontend is committed locally at `465d2fa`, including correction
of accidental test-file line-ending churn. Parent review covers source, screenshots and the two
bounded backend recovery findings. Artwork production continues through GPT Images; the remaining
catalogue and broader everyday journeys are unfinished. Live in-app checks remain deferred.
The nine prepared Ready character editions now carry the existing shared visual records for
their exact authored starter identities. Their builds, equipment, prose and retained Plan
bindings are unchanged; only visuals, character hashes and artwork provenance were added.
Customize also passes those visual records into saved editable Character Files. The actual
Lite Ready-party resolve/customize check passes, including immutable source/build comparisons.
Broader everyday journey
qualification remains open. The unfinished-draft `candidate_unavailable` finding is fixed for new
exports: confirmation references now retain their candidate and package closure. Recovery checks
cover fresh/shared libraries, differing local activation markers, continuation and reopen. Shared
candidate reuse preserves recipient metadata and rejects different content. Image handoff and
actual Lite prepared-art transfer checks now retain the normal candidate reference. Older incomplete
archives that omitted it require a fresh export from the source dataset.
These source/scripted checks do not establish completion of the ordinary
workflows. The owner's latest selection keeps live in-app checks pending. Of the 366 prepared assets,
355 were produced with the built-in GPT Images tool and eleven are the unchanged supplied Siren
master sheets. Asset preparation is separate from Sylvia's deferred native check.
Live Sylvia subscription, installed-app
and TD trailer acceptance remain unverified. Merge follows the Pneuma IO rulebook's delivery and
cleanup sequence.

**9 November 2026: optional onboarding and the complete tutorial suite.** The owner selected a new
PR for all independently accessible lessons and a guided welcome, AI connection, Ready campaign
and first-scene journey. Dot Desk work item **Build the tutorial suite and current new-player
onboarding** records the approved scope. Implementation uses an isolated checkout and preserves
the separate Alpha build owner. The attract-mode showcase and no-AI battles remain future work.
Source/browser checks qualify this PR's UI behavior; genuine-provider, native installed-app,
visual approval, merge and release acceptance remain separate.

Sylvia GM has a public source repository and four published Alpha releases. The releases page
is the download destination; an older installed build does not verify newer source.

The approved Fable refactor preserves game behavior while improving local checks, removing unused
code, organizing command routing and the GM turn, and correcting stale documentation. It also
speeds the Ruleset inspector. Its private-content prerequisite removes Trigger Discipline vault
notes and Siren Protocol payloads from current public fixtures. Historical copies remain in Git
history and old release source archives; this work does not rewrite history.

| Work | Current boundary |
|---|---|
| Frontend overhaul, PR #28 | Merged on main `6526904` before PR #6. PR #6 owns its composed TD qualification; source/browser evidence does not by itself establish final installed acceptance. |
| Trigger Discipline integration, PR #6 | Separate active owner; checked draft `573b015` combines TD `72522a8` and main-targeted hotfix `11c279d`, with no conflict. TD package/frontend/Rust bytes retain native-tested `4b438d7` provenance; build 17 and its native replay are separate evidence. Eight final source checks and Full/AP2 owner-process plus loopback narration checks pass at `573b015`. Host desktop control, balancing, merge and release remain held. Privacy removal is complete; public packages exclude canonical vault text, and full public-session adequacy without private prose/future Rulebook remains unproven. The final combined refactor/hotfix candidate needs rebuilding and its own selected native qualification. |
| Refactor, [PR #33](https://github.com/pneuma-io/sylvia-gm/pull/33) | Stages 1-8, including stage 7A, are implemented and undergoing final local qualification. The PR records exact checked heads, commands, outcomes, strict suspected defects and remaining baseline gaps. Independent acceptance and the final combined candidate remain open. Stage 9 (`app.js` and WebUI script policy) waits for PR #6 to merge; PR #28 is already merged. Broad formatting remains deferred while open PRs change source. |
| SRD Full, PR #3 | Parked, inactive work; it does not expand the 117-spell Lite launch roster. |
| GM memory and casting hotfix | Separate draft [PR #36](https://github.com/pneuma-io/sylvia-gm/pull/36), checked main-targeted source `11c279d`; GitHub main remains `6526904`. The source handoff includes durable GM memory, usable advertised tools and healing input/refusal repairs. Fourteen scripted-provider request contracts and 285 focused GM cases pass on the separate checked composition. Its Lite 0.3.18 source and the Alpha4 backport Lite 0.3.17 native evidence have distinct provenance. Full combined/build/native/genuine-provider acceptance remains separate; no merge or release is inferred. |
| SylviaBench | Outside this refactor. |

Tutorials/onboarding, desktop updates, local diagnostics and compatible campaign Ruleset updates
have implementation in the repository. Their existence does not approve a new release or a new
deployment. Ongoing Trigger Discipline work is no longer a blanket post-Alpha deferral.

For the removed historical checkpoints, use
`git show 39aac5168d7e95f55a444efae98df0db82474616:docs/plans/alpha-rebuild-readiness-plan.md`.

## 2. Milestone record

| Milestone | Status and limits |
|---|---|
| Desktop/owner, Builder, persistence and recovery | Implemented; changes still need affected checks and final-candidate acceptance. |
| SRD Lite and bundled campaigns | The supported roster remains exactly 117 spells without consumables. Preserve exact package pins, existing campaigns and the multi-ruleset architecture. Full remains inactive. |
| Original bundled Ruleset | Removed from Alpha requirements. |
| Initial public publication | Completed; the public repository and Alpha releases supersede the old “private/publication blocked” status. |
| Next release | Requires local combined-source checks and exact installer/portable acceptance. No open PR or historical milestone grants publication approval. |

## 3. Advertised Alpha journeys

Release work protects these outcomes:

1. Start a general Builder chat, converse, create content, Stop, steer, switch models,
   close, reopen and continue.
2. Save private provider configurations, discover or enter models, and connect a supported
   hosted or already-running local provider without exposing credentials.
3. Start a supported Ready campaign or The Rift Awakening, play with authoritative mechanics,
   save, close, reopen, back up and recover.
4. Start a No Ruleset campaign without invented dice, classes, HP or other unselected mechanics.
5. Install, launch, restart and uninstall the Windows application without orphan owners or
   renderer-owned authority. Protect installed/portable data selection and same-dataset refusal.
6. Present usable controls and truthful compatibility information without severe flicker or
   internal jargon in ordinary settings.

## 4. Local verification and release sequence

Checks run locally. The GitHub workflow is manual-only; pushing a branch or opening a PR does
not start GitHub Actions. A PR is ready for review when its description records the locally
checked head commit, commands, results, elapsed time and checks not run.

1. Use a branch for each selected change and run its affected checks locally. Commit and push
   the source; keep the PR description tied to the exact tested head.
2. Bring current `main` into the PR before merging. Resolve conflicts and rerun affected checks
   on the combined result. Tests of two separate branches do not qualify their combination.
3. Merge the selected PRs in the agreed order, then run the full deterministic gate on the
   resulting `main`. Include local Python lint/type checks and affected browser, packaging and
   Rust checks. Record failures and limitations explicitly; baseline failures are not new passes.
4. Build the installer and portable ZIP from that clean, checked commit. Bind source and artifact
   identities with the existing release recipe and required notices and identity checks.
5. Check the actual candidate: Ready campaign turns including dice, Stop/Retry, Builder
   continuation across restart, New Campaign, Models and Rulesets, detached panels and WebUI
   if used. Keep genuine-provider and native-input evidence separate from scripted fixtures.
6. Publish only after owner approval, using the checked artifacts. Later source changes require
   a new candidate and affected verification. Keep the preceding verified deliverable available.

Development checks have no whole-run wall-time cutoff. `--ci` is only the explicitly selected
future budget mode. Missing or failed required checks fail the gate. Known defects must be named,
with their own reproduction; they cannot be hidden as generic skips or called passing behavior.

The deterministic gate cannot establish real provider compatibility, native interaction, or
installer correctness on its own. Follow the
[development process](../operations/quality-preserving-development-process.md) for those checks.

## 5. Verification relevance fence

For a change, verify its owning boundary, representative direct consumers, and persistence,
privacy, packaging or installed behavior where affected. Completed specialist matrices do not
automatically direct new work. A repository-wide run is appropriate for this cross-cutting
refactor; it is not a standing requirement for every later small edit.

Desktop testing uses the environment selected for the current run. The old VM is retired.
Preserve current desktop holds. Historical evidence retains its original source, provider and
input limitations and cannot qualify changed bytes.

## 6. Follow-ups needing their own selection

- A persistent product Ruleset cache, after reviewing this refactor's measurements.
- A reproducible Lite package build and any specialization of unused Full spell machinery.
- New Campaign integrity-sweep timing and any change to that data-safety mechanism.
- Retirement of the older Builder agent loop; decomposition of the authority/store classes.
- Rust command wrappers and command-bus/supervisor changes, with a security review.
- Current provider catalog/reasoning tables and possible fallback paths.
- Additional native platforms, Windows 10, subscriptions, paid signing, cloud play, marketplace,
  executable plugins, and features listed as planned in [proposals](../../proposals/README.md).

Siren Protocol remains private. Trigger Discipline public gameplay limits belong to its own
integration work item. Neither selects additional mechanics in this refactor.

## 7. Repository and release boundary

Exactly one worker writes a checkout; keep work items isolated and preserve unrelated edits.
Dot Desk holds current work items and decisions, GitHub holds project source, and the Pneuma IO
Rulebook governs delivery, reporting, cleanup and merge order. The current human task supplies
side-effect authority; this plan is not standing permission to publish, spend, access private
campaigns, rewrite history or perform destructive data operations.
