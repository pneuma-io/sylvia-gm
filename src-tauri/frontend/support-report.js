/* Deliberate local report preview. Exact preview bytes are frozen by the owner. */
(() => {
  'use strict';
  window.SylviaSupportReport = {
    init(bridge) {
      const get = id => document.getElementById(id);
      const preview = get('local-report-preview');
      const status = get('local-report-status');
      const prepare = get('preview-local-report-btn');
      const save = get('save-local-report-btn');
      const cancel = get('cancel-local-report-btn');
      const fields = ['local-report-kind', 'local-report-reproduction', 'local-report-feedback'].map(get);
      let pending = null;
      let revision = 0;
      let busy = false;
      let saving = false;
      let saveUncertain = false;
      const savingMessage = 'Saving report. Settings will stay open until the result is available.';

      // Invalidating a preview also handles an in-flight response after edits/close.
      function discard(clear = false, cancelPending = true) {
        revision += 1;
        const old = pending;
        pending = null;
        preview.hidden = true;
        get('local-report-content').textContent = '';
        if (clear) {
          fields[1].value = '';
          fields[2].value = '';
          status.textContent = '';
        }
        if (old && cancelPending) void bridge.cancel_local_report(old).catch(() => {});
      }

      function setBusy(value) {
        busy = value;
        [prepare, save, cancel, ...fields].forEach(control => { control.disabled = value; });
      }

      function setSaving(value) {
        saving = value;
        get('close-settings-btn').disabled = value;
        document.querySelectorAll('[data-settings-tab]').forEach(control => { control.disabled = value; });
      }

      fields.forEach(field => field.addEventListener('input', () => {
        if (busy) return;
        discard();
        status.textContent = 'Preview your changes before saving.';
      }));
      cancel.addEventListener('click', () => {
        if (busy) return;
        discard(true);
        status.textContent = saveUncertain
          ? 'Preview discarded. Check your chosen file for the previous save attempt.'
          : 'Cancelled. No report was saved.';
        prepare.focus();
      });
      prepare.addEventListener('click', async () => {
        if (busy) return;
        discard();
        saveUncertain = false;
        const current = revision;
        setBusy(true);
        status.textContent = 'Preparing local preview.';
        try {
          const result = await bridge.preview_local_report({
            kind: fields[0].value, reproduction: fields[1].value, feedback: fields[2].value,
          });
          if (current !== revision) {
            void bridge.cancel_local_report(result.preview_id).catch(() => {});
            return;
          }
          pending = result.preview_id;
          get('local-report-content').textContent = result.content;
          preview.hidden = false;
          status.textContent = `${result.bytes.toLocaleString()} bytes. Review the exact file below.`;
        } catch (_) {
          if (current === revision) status.textContent = 'Could not prepare the report. Try again.';
        } finally {
          setBusy(false);
          if (current === revision && pending) save.focus();
        }
      });
      save.addEventListener('click', async () => {
        if (busy || !pending) return;
        const selected = pending;
        const current = revision;
        let finished = false;
        setBusy(true);
        setSaving(true);
        status.textContent = savingMessage;
        try {
          const result = await bridge.save_local_report(selected);
          if (current !== revision) return;
          discard(true, false);
          finished = true;
          saveUncertain = false;
          status.textContent = result.status === 'saved'
            ? 'Report saved to your chosen file.' : 'Cancelled. No report was saved.';
        } catch (_) {
          if (current === revision) {
            saveUncertain = true;
            status.textContent = 'Could not confirm whether the report was saved. Check your chosen file before retrying.';
          }
        } finally {
          setSaving(false);
          setBusy(false);
          if (finished) prepare.focus();
          else if (current === revision && pending) save.focus();
        }
      });
      return { close() {
        if (saving) {
          status.textContent = savingMessage;
          return false;
        }
        const result = saveUncertain
          ? 'Preview discarded. Check your chosen file for the previous save attempt.'
          : pending || busy ? 'Preview cancelled. No report was saved.' : status.textContent;
        discard(true);
        status.textContent = result;
        return true;
      } };
    },
  };
})();
