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

Limits: 256 chats, 1,000 messages per chat, 8,000 characters per player message. If the card is
removed from the library the chat stays readable but cannot continue.

## Not included yet

Deleting or renaming chats, regenerating or editing replies, alternative greetings, lore
activation, live token streaming in the chat view, and inclusion in database backups or campaign
exports.
