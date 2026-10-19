# Dictation

Status: PL-11 composer and lifecycle implementation in progress. Backend selection and native microphone acceptance remain open; this draft is not a working speech-recognition delivery yet.

## User outcome

A mic button beside the ordinary message input lets the player explicitly start and stop recording, obtain a transcript, edit or re-dictate it, and deliberately submit the reviewed message. Preserve typed input and existing drafts. This is dictation like a message-composer mic button, not full voice play, spoken GM responses or a realtime voice conversation.

The core journey is an ordinary player action in GM mode. Reuse the same composer behavior for any other input surface selected during implementation; do not invent separate speech workflows for every screen.

## Current baseline

The shared GM/Builder composer now has keyboard-operable microphone, cancel, undo and re-dictation controls, visible status announcements, cursor insertion and editable transcript review. An unchanged draft receives the transcript at its saved selection. Later typing keeps its text and receives a separate editable transcript with explicit insertion. Session changes, cancellation and timeouts invalidate old results. Only ordinary Send submits a message.

The frontend capture contract requires explicit start, stop-to-transcript and cancellation, with abort cleanup. Existing model-provider adapters have no transcription route. The native bridge implementation is pending the owner's local-versus-hosted backend selection. Until that is selected and connected, the mic reports that dictation is unavailable and keeps typing usable.

## Proposed flow and boundaries

- Provide visible, keyboard-operable Start, Stop and Cancel controls. A sustained button hold may be optional, but a start/stop alternative is required. Give accessible names and announcements for idle, recording, transcribing, review-ready and failed states.
- Request microphone access only from an explicit user action. Show when capture is active and stop capture on cancel, closure or relevant session change. Permission denial, missing device and provider failure must leave ordinary typing usable.
- Preserve the existing draft. Prefer insertion at the selected cursor/selection; show a reviewable result and allow undo/re-dictation. Confirm this combine behavior against the actual composer before implementation. A delayed transcript must never land in another session or overwrite text typed meanwhile.
- Transcription does not send a player message. The user reviews/corrects the text and presses the normal Send control; the request is submitted exactly once through the existing path.
- Select the actual transcription backend, supported languages/platforms, credentials and any network/cost disclosure before connecting it. No provider is forced, purchased or configured by this plan. Do not silently transmit recordings, enable continuous listening, keep audio indefinitely or upload gameplay.
- Define temporary audio cleanup and redacted failure diagnostics. Never log raw audio, credentials or private transcripts as routine engineering output.

## Acceptance

1. With disposable speech, start/stop recording, review/edit the transcript and submit the intended action exactly once. Assert no submission before Send.
2. Exercise cursor insertion, an existing draft, text edited while transcription is pending, re-dictation, undo, cancel and session switching. Retain the correct draft and session identity.
3. Test permission refusal, unavailable microphone, empty/invalid transcript, interrupted capture, failed/slow transcription and explicit retry without duplicate paid requests.
4. Complete the journey using keyboard and assistive controls without holding a button. Check clear recording state, accessible announcements and ordinary typing throughout.
5. Verify the selected real microphone/provider path on disposable data and the affected installed frontend. Mocked transcripts alone do not establish native capture or accessibility acceptance.

## Decisions for implementation

Choose the supported transcription integration and privacy/retention behavior, then settle the precise transcript/draft merge interaction against the current composer. These choices do not expand the scope to TTS, full voice play, ambient recording or automatic message submission.

The proposed first backend is on-device Windows speech recognition using an installed language recognizer, without new paid API configuration or audio upload. This remains a recommendation awaiting owner selection. A metadata-only probe found an English (UK) recognizer in the selected Windows environment; no microphone was opened. Hosted transcription would require explicit provider, privacy and cost selection.

## Current verification

- `node --test tests/frontend_dictation.test.cjs` covers selection insertion, once-only completion, undo/re-dictation, draft edits during transcription, cancellation, session switching, permission/device/provider failures, empty results and timeouts using controlled fixtures.
- `node --test tests/frontend_dictation.browser.cjs tests/frontend_shell.browser.cjs` passes in Edge with synthetic bridge responses: keyboard start/stop/cancel, editable review/correction, stale-result protection, no automatic send, exactly one ordinary send, model-control spacing and short-window layout.
- `node --test tests/frontend_saved_sessions.test.cjs tests/frontend_agent_activity.test.cjs` preserves the existing draft/session and activity checks.
- Real microphone, real transcription backend, assistive-technology operation and normal native application acceptance have not run. Browser fixtures do not establish those results.

## Full specification and implementation boundary

Specification reference: PL-11 - `9290fded-752a-4e8f-8da5-f7585a726c83`. Desk revision 8 and all complete original ticket versions were retrieved before implementation. The original archive matched its supplied SHA-256. The complete Desk specifications and linked original acceptance remain authoritative; these public notes are an implementation summary. Daniel's implementation selection supersedes the earlier planning-only boundary. The coordinator owns Desk reconciliation.

This checkpoint changes the composer and its focused tests. Backend connection and native acceptance are still required to complete the feature. No credentials, installation, paid calls, actual user audio, merge or release are included. No GitHub Actions before November 2026.
