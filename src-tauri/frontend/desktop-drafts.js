/* Unsent input durability. This module stores text only and never submits model or game work. */
(() => {
  'use strict';

  function mount(bridge, { sessionDrafts, builderQuestionDrafts,
    failed = () => {}, saved = () => {} }) {
    let ready = false, dirty = false, pending = null, timer = null;
    let updateView = null;
    const supported = typeof bridge.load_desktop_drafts === 'function' &&
      typeof bridge.save_desktop_drafts === 'function';

    function snapshot() {
      const ids = new Set([...sessionDrafts.keys(), ...builderQuestionDrafts.keys()]);
      return { format_version: 1, ...(updateView ? { update_view: { ...updateView } } : {}),
        sessions: [...ids].map(sessionId => {
        const draft = sessionDrafts.get(sessionId) || {};
        return { session_id: sessionId, text: draft.text || '', ooc_text: draft.oocText || '',
          ooc_open: Boolean(draft.oocOpen),
          questions: [...(builderQuestionDrafts.get(sessionId) || [])].map(([key, fields]) =>
            ({ question_key: key, ...fields })),
        };
      }) };
    }

    // One writer: flush waits for changes made while an earlier save was in flight as well.
    async function flush() {
      if (timer !== null) { window.clearTimeout(timer); timer = null; }
      if (!supported || !ready) throw new Error('Draft storage is unavailable. Keep this window open.');
      if (pending) { await pending; return flush(); }
      if (!dirty) return;
      pending = (async () => {
        while (dirty) {
          const value = snapshot();
          dirty = false;
          try { await bridge.save_desktop_drafts(value); }
          catch (error) { dirty = true; failed(error); throw error; }
        }
      })();
      try { await pending; }
      finally { pending = null; }
      saved();
    }

    function changed() {
      dirty = true;
      if (!ready || timer !== null) return;
      timer = window.setTimeout(() => {
        timer = null;
        void flush().catch(() => {}); // Error is reported once; retry requires editing or flush.
      }, 250);
    }

    async function load() {
      if (!supported) return false;
      try {
        const value = await bridge.load_desktop_drafts();
        if (value?.format_version !== 1 || !Array.isArray(value.sessions))
          throw new Error('Saved drafts are incompatible. Keep this window open.');
        // Initialization completes before compose controls are wired; no input is overwritten.
        for (const row of value.sessions) {
          if (row.text || row.ooc_text || row.ooc_open) sessionDrafts.set(row.session_id,
            { text: row.text, oocText: row.ooc_text, oocOpen: row.ooc_open });
          if (row.questions.length) builderQuestionDrafts.set(row.session_id,
            new Map(row.questions.map(({ question_key, ...fields }) => [question_key, fields])));
        }
        updateView = value.update_view || null;
        ready = true;
        saved();
        return true;
      } catch (error) { failed(error); return false; } // Failed reads cannot overwrite retained text.
    }

    async function rememberUpdate(operationId, view) {
      updateView = { operation_id: operationId, ...view };
      changed();
      await flush();
    }

    return { load, changed, flush, rememberUpdate,
      updateView: operationId => updateView?.operation_id === operationId ? { ...updateView } : null };
  }

  window.SylviaDesktopDrafts = { mount };
})();
