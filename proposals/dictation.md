# Dictation

Status: PL-11 composer and local Windows recognition implemented. Current native-shell rebuild and physical microphone acceptance remain open.

## User outcome

A mic button beside the ordinary message input lets the player explicitly start and stop recording, obtain a transcript, edit or re-dictate it, and deliberately submit the reviewed message. Preserve typed input and existing drafts. This is dictation like a message-composer mic button, not full voice play, spoken GM responses or a realtime voice conversation.

The core journey is an ordinary player action in GM mode. Reuse the same composer behavior for any other input surface selected during implementation; do not invent separate speech workflows for every screen.

## Current baseline

The shared GM/Builder composer now has keyboard-operable microphone, cancel, undo and re-dictation controls, visible status announcements, cursor insertion and editable transcript review. An unchanged draft receives the transcript at its saved selection. Later typing keeps its text and receives a separate editable transcript with explicit insertion. Session changes, cancellation and timeouts invalidate old results. Only ordinary Send submits a message.

The frontend capture contract requires explicit start, stop-to-transcript and cancellation, with abort cleanup. Existing model-provider adapters have no transcription route. Daniel selected local-only recognition. The native host uses the installed Microsoft Windows desktop speech recognizer through System.Speech in Windows PowerShell. It lists installed languages and has no hosted fallback, credentials, downloads or paid requests. Other platforms or missing Windows speech support return an unavailable state while typing remains usable.

The host retains the existing trusted-main-window checks. It owns one temporary child process, bounded replies and startup/stop/lifetime deadlines. Cancel works during startup and transcription; old cancellation IDs cannot stop a newer capture. Closing the main window or app releases capture. The production helper creates no audio files and disables acoustic adaptation for the recognizer instance without changing persisted user settings. Audio and transcripts do not enter routine diagnostics. Production code contains no fixture transcript controls.

Sending active-turn Builder steering cancels dictation before waiting for acceptance, so transcription cannot repopulate a sent composer or change the draft during that wait. Rejected steering keeps the typed draft.

## Proposed flow and boundaries

- Provide visible, keyboard-operable Start, Stop and Cancel controls. A sustained button hold may be optional, but a start/stop alternative is required. Give accessible names and announcements for idle, recording, transcribing, review-ready and failed states.
- Request microphone access only from an explicit user action. Show when capture is active and stop capture on cancel, closure or relevant session change. Permission denial, missing device and provider failure must leave ordinary typing usable.
- Preserve the existing draft. Insert at the selected cursor/selection; show a reviewable result and allow undo/re-dictation. A delayed transcript must never land in another session or overwrite text typed meanwhile.
- Transcription does not send a player message. The user reviews/corrects the text and presses the normal Send control; the request is submitted exactly once through the existing path.
- Use the selected on-device Windows backend and its installed languages. Keep the selected language and local-audio explanation in collapsed dictation settings. Do not transmit recordings, enable continuous listening or retain audio.
- Define temporary audio cleanup and redacted failure diagnostics. Never log raw audio, credentials or private transcripts as routine engineering output.

## Acceptance

1. With disposable speech, start/stop recording, review/edit the transcript and submit the intended action exactly once. Assert no submission before Send.
2. Exercise cursor insertion, an existing draft, text edited while transcription is pending, re-dictation, undo, cancel and session switching. Retain the correct draft and session identity.
3. Test permission refusal, unavailable microphone, empty/invalid transcript, interrupted capture, failed/slow transcription and explicit retry without duplicate paid requests.
4. Complete the journey using keyboard and assistive controls without holding a button. Check clear recording state, accessible announcements and ordinary typing throughout.
5. Verify the selected real microphone/provider path on disposable data and the affected installed frontend. Mocked transcripts alone do not establish native capture or accessibility acceptance.

## Selected integration

Local-only Windows dictation is selected. Only explicit Start opens audio input. This does not expand the scope to TTS, full voice play, ambient recording or automatic message submission.

A metadata-only probe found an installed English (UK) recognizer. The production helper, with its microphone-input line replaced privately by file input, transcribed an offline synthesized WAV. It returned `I walk north and opened the door` for `I walk north and open the door.` This proves real local recognition with a visible correction need, without opening a microphone or speaker. It does not prove physical capture. No new engine or language was installed.

Run `.venv/Scripts/python.exe tests/windows_dictation_smoke.py` for this controlled recognition check and the helper's permission/device error mapping. The latter substitutes controlled exceptions for the audio-input call and asserts safe error codes without opening a microphone. These checks do not establish actual Windows microphone permission behavior.

## Current verification

- `node --test tests/frontend_dictation.test.cjs` covers selection insertion, once-only completion, undo/re-dictation, draft edits during transcription, cancellation, session switching, permission/device/provider failures, empty results and timeouts using controlled fixtures.
- `node --test tests/frontend_dictation.browser.cjs tests/frontend_shell.browser.cjs` passes in Edge with synthetic bridge responses: keyboard start/stop/cancel, language/privacy disclosure, editable review/correction, stale-result protection, no automatic send, exactly one ordinary send, active-turn Builder Enter steering, model-control spacing and short-window layout.
- `node --test tests/frontend_saved_sessions.test.cjs tests/frontend_agent_activity.test.cjs` preserves the existing draft/session and activity checks, including accepted and rejected steering with transcription completing during or after the request.
- The Rust module has seven process-controller tests for cancellation before/during start and transcription, one active capture, one Stop result, old cancellation IDs, bounded waits and redacted malformed/failure replies. A small private harness compiled the exact module without the full desktop dependency graph.
- Earlier native UIA checks and screenshots cover source `f218196c2f9cd0317185cf1fd0abb90e33345ebb` with a real source owner and controlled transcript fixtures. They retain that qualification; they do not validate the later native recognition bridge.
- Current full native build/launch, physical microphone operation, audible assistive-technology announcements and installed/packaged-owner acceptance remain unverified. Browser fixtures and isolated controller compilation do not replace these checks.

## Full specification and implementation boundary

Specification reference: PL-11 - `9290fded-752a-4e8f-8da5-f7585a726c83`. Desk revision 8 and all complete original ticket versions were retrieved before implementation. The original archive matched its supplied SHA-256. The complete Desk specifications and linked original acceptance remain authoritative; these public notes are an implementation summary. Daniel's implementation selection supersedes the earlier planning-only boundary. The coordinator owns Desk reconciliation.

This change owns the generic composer and native speech capture. Ruleset content, SRD selection/expansion, schemas, campaign data, model adapters, dependency versions and packaged owner bytes are unchanged. No installed playable copy was modified. Native acceptance remains required. No credentials, installation, paid calls, actual user audio, merge or release are included. No GitHub Actions before November 2026.
