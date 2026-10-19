/* Dictation owns temporary capture and draft edits, never message submission. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SylviaDictation = api;
})(typeof window === 'undefined' ? globalThis : window, () => {
  'use strict';

  const failures = {
    permission: 'Microphone permission was denied. Allow microphone access, then try again. Your draft is kept.',
    device: 'No microphone is available. Connect a microphone, then try again. Your draft is kept.',
    unavailable: 'Dictation is unavailable. You can keep typing. Your draft is kept.',
    provider: 'Transcription failed. Try dictating again. Your draft is kept.',
    empty: 'No speech was recognised. Try dictating again. Your draft is kept.',
    timeout: 'Dictation timed out and the microphone was stopped. Try again. Your draft is kept.',
  };

  // A backend starts only on explicit action and returns stop() -> text plus cancel().
  // It must release capture on abort and keep audio/transcripts out of routine diagnostics.
  function create({ backend, context, draft, replace, changed, timer = setTimeout, clear = clearTimeout }) {
    let active = null;
    let review = null;
    let undo = null;
    let status = { phase: 'idle', message: 'Dictate a message, then review it before Send.' };

    const snapshot = () => ({ ...status, transcript: review?.text || '',
      canUndo: Boolean(undo && undo.context === context() && undo.after === draft().text &&
        undo.revision === draft().revision) });
    const publish = (phase, message) => { status = { phase, message }; changed(snapshot()); };
    const current = (attempt) => active === attempt && attempt.context === context();
    const release = (attempt) => {
      clear(attempt.timer);
      attempt.abort.abort();
      try { attempt.capture?.cancel(); } catch (_) { /* Capture cleanup must not erase the draft. */ }
    };
    const fail = (attempt, code) => {
      if (!current(attempt)) return;
      active = null;
      release(attempt);
      publish('error', failures[code] || failures.provider);
    };
    const deadline = (attempt, duration) => {
      clear(attempt.timer);
      attempt.timer = timer(() => fail(attempt, 'timeout'), duration);
    };

    function cancel(message = 'Dictation cancelled. Your draft is kept.') {
      const attempt = active;
      active = null;
      review = null;
      if (attempt) release(attempt);
      publish('idle', message);
    }

    function sync() {
      if ((active && active.context !== context()) || (review && review.context !== context()) ||
          (undo && undo.context !== context())) {
        undo = null;
        cancel('Dictation stopped after leaving the message. Your draft is kept.');
      } else changed(snapshot());
    }

    // Insert exactly once. A stale result needs an explicit insert at the current selection.
    function insert(text, selection, expected) {
      if (expected.context !== context()) return false;
      const before = draft();
      const start = Math.max(0, Math.min(selection.start, before.text.length));
      const end = Math.max(start, Math.min(selection.end, before.text.length));
      const left = before.text.slice(0, start), right = before.text.slice(end);
      const addition = (left && !/\s$/.test(left) ? ' ' : '') + text +
        (right && !/^\s|^[.,!?;:)]/.test(right) ? ' ' : '');
      const after = left + addition + right;
      replace(after, start + addition.length);
      undo = { context: context(), before: before.text, after, revision: draft().revision,
        start, end: start + addition.length };
      review = null;
      publish('ready', 'Dictation ready. Review or edit your message, then press Send.');
      return true;
    }

    async function start({ redo = false } = {}) {
      if (active || !context()) return;
      const initial = draft();
      const selection = redo && snapshot().canUndo ? { start: undo.start, end: undo.end } :
        { start: initial.start, end: initial.end };
      const attempt = { context: context(), initial, selection, abort: new AbortController(), capture: null };
      active = attempt;
      review = null;
      publish('requesting', 'Starting microphone. You can cancel or keep typing.');
      deadline(attempt, 30000);
      try {
        const capture = await backend.start({ signal: attempt.abort.signal });
        attempt.capture = capture;
        if (!current(attempt)) { release(attempt); return; }
        publish('recording', 'Recording. Press Stop dictation to transcribe, or Cancel dictation.');
        deadline(attempt, 120000);
      } catch (error) { fail(attempt, typeof error === 'string' ? error : error?.code); }
    }

    async function stop() {
      const attempt = active;
      if (!attempt?.capture || status.phase !== 'recording') return;
      publish('transcribing', 'Transcribing. The microphone is stopping. You can cancel or keep typing.');
      deadline(attempt, 60000);
      try {
        const result = await attempt.capture.stop();
        if (!current(attempt)) { release(attempt); return; }
        const text = typeof result === 'string' ? result.trim() : '';
        if (!text) { fail(attempt, 'empty'); return; }
        active = null;
        release(attempt);
        const latest = draft();
        if (latest.revision === attempt.initial.revision && latest.text === attempt.initial.text) {
          insert(text, attempt.selection, attempt);
        } else {
          review = { context: attempt.context, text };
          publish('ready', 'Transcript ready. Your message changed, so review the transcript below and insert it when ready.');
        }
      } catch (error) { fail(attempt, typeof error === 'string' ? error : error?.code); }
    }

    function insertReview(text) {
      if (!review || active || review.context !== context()) return false;
      if (!String(text).trim()) return false;
      const selection = draft();
      return insert(String(text).trim(), selection, review);
    }

    function undoInsertion() {
      if (active || !snapshot().canUndo) return;
      replace(undo.before, undo.start);
      undo = null;
      publish('idle', 'Dictation undone. Your previous draft is restored.');
    }

    return { start, stop, cancel, sync, snapshot, insertReview, undo: undoInsertion };
  }

  return { create };
});
