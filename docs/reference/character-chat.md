# Character chat

Character chat is a direct one-to-one conversation with a saved Universal character card. It is
not a campaign: there is no Game Master, Setting, Ruleset, tool, dice or engine, and nothing in a
character chat changes campaigns, cards or packages.

## Flow

Home → Character chat lists the saved Universal character cards (Settings → Packages → Import
Character Card adds them) and saved chats. Starting a chat records the exact card identity
(`artifact_id` and `content_sha256`), the chosen model and an optional name for the player
(default "You"). A V2 card's default greeting becomes the character's first message; a card
without one lets the player speak first. Saved chats reopen from the same list.

## What the model receives

Each reply is one plain streamed request with no tools: a system message, a bounded recent
history (newest messages within 24,000 characters), then the new message. The system message
asks for in-character replies only, never the player's words or actions, and frames the card as
reference data that cannot override those rules. It includes the card's summary, aliases,
pronouns, description, personality, appearance, voice, history, relationships, scenario, context,
portrayal guidance and dialogue examples (as style reference); a V1 card contributes its summary
and authored profile. `{{char}}`, `{{user}}`, `<BOT>` and `<USER>` placeholders become the
character's and player's names. Lore entries, foreign settings, prompt overrides and assets stay
inert. Reasoning text is never shown or saved.

## Saving

Each chat is one JSON file in `character-chats/` beside the character profile library, replaced
atomically after every completed reply. The player's message and the reply are saved together
only when the reply succeeds; on failure nothing is saved and the text stays in the composer.
Every send carries an operation identity and the expected message count, so a repeated send
returns the saved chat instead of asking the model twice, and a send based on an older copy is
refused. One reply per chat runs at a time.

Every save is first read back with the same checks a reopen uses, so a chat is never written in a
form that cannot open again. Player text containing NUL characters or unpaired surrogates is refused
before the model is asked; replies have them removed or replaced. Reads and writes share one lock in
the owner, and the atomic replace retries briefly while another program holds the file.

Limits: 256 chats, 1,000 messages per chat, 8,000 characters per player message, 65,536 per reply.
If the card is removed from the library the chat stays readable but cannot continue. Importing the
identical card again (same content hash) lets it continue.

In the dialog, an unconfirmed send says so and is resent under the same identity. A chat changed in
another window, or whose card was removed, is reloaded with the typed text kept.

## Not included yet

Deleting or renaming chats, regenerating or editing replies, alternative greetings, lore
activation, live token streaming in the chat view, and inclusion in database backups or campaign
exports.
