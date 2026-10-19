# Dictation

Status: selected planning scope, PL-11. The feature is not implemented by this draft.

## User outcome

A mic button beside the ordinary message input lets the player explicitly start and stop recording, obtain a transcript, edit or re-dictate it, and deliberately submit the reviewed message. Preserve typed input and existing drafts. This is dictation like a message-composer mic button, not full voice play, spoken GM responses or a realtime voice conversation.

The core journey is an ordinary player action in GM mode. Reuse the same composer behavior for any other input surface selected during implementation; do not invent separate speech workflows for every screen.

## Current baseline

The current desktop frontend has typed message/Builder input and a Tauri host bridge. The selected frontend file does not expose a reviewed recording/transcription journey. Ordinary message submission and session/request identity handling should be reused. This source inspection is not a fresh microphone or installed-app test, and a keyword check is not proof that every related helper is absent.

## Proposed flow and boundaries

- Provide visible, keyboard-operable Start, Stop and Cancel controls. A sustained button hold may be optional, but a start/stop alternative is required. Give accessible names and announcements for idle, recording, transcribing, review-ready and failed states.
- Request microphone access only from an explicit user action. Show when capture is active and stop capture on cancel, closure or relevant session change. Permission denial, missing device and provider failure must leave ordinary typing usable.
- Preserve the existing draft. Prefer insertion at the selected cursor/selection; show a reviewable result and allow undo/re-dictation. Confirm this combine behavior against the actual composer before implementation. A delayed transcript must never land in another session or overwrite text typed meanwhile.
- Transcription does not send a player message. The user reviews/corrects the text and presses the normal Send control; the request is submitted exactly once through the existing path.
- Select the actual transcription backend, supported languages/platforms, credentials and any network/cost disclosure before connecting it. No provider is forced, purchased or configured by this plan. Do not silently transmit recordings, enable continuous listening, keep audio indefinitely or upload gameplay.
- Define temporary audio cleanup and redacted failure diagnostics. Never log raw audio, credentials or private transcripts as routine engineering output.

## Acceptance and future tests

1. With disposable speech, start/stop recording, review/edit the transcript and submit the intended action exactly once. Assert no submission before Send.
2. Exercise cursor insertion, an existing draft, text edited while transcription is pending, re-dictation, undo, cancel and session switching. Retain the correct draft and session identity.
3. Test permission refusal, unavailable microphone, empty/invalid transcript, interrupted capture, failed/slow transcription and explicit retry without duplicate paid requests.
4. Complete the journey using keyboard and assistive controls without holding a button. Check clear recording state, accessible announcements and ordinary typing throughout.
5. Verify the selected real microphone/provider path on disposable data and the affected installed frontend. Mocked transcripts alone do not establish native capture or accessibility acceptance.

## Decisions for implementation

Choose the supported transcription integration and privacy/retention behavior, then settle the precise transcript/draft merge interaction against the current composer. These choices do not expand the scope to TTS, full voice play, ambient recording or automatic message submission.

## Full specification and implementation boundary

Planning references: PL-11 — `9290fded-752a-4e8f-8da5-f7585a726c83`. The complete current Desk specifications and their linked original acceptance/source material remain authoritative. Retrieve all of them through authorized access before implementation; these public notes are an index and selected scope, not a replacement handoff. Daniel's latest selection here supersedes conflicting older framing. The coordinator owns Desk reconciliation.

This PR changes one proposal document only. No feature implementation, external configuration, credentials, installation, paid calls, tests of the feature, merge or release is delivered. No GitHub Actions before November 2026. Future implementation and final-byte acceptance remain separate work.
