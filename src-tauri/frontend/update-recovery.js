/* Recovery has a separate fixed bridge and never initializes campaign or model controls. */
(function () {
  'use strict';
  function mount(root, bridge) {
    const node = name => root.querySelector(`#recovery-${name}`);
    let snapshot = null, pending = false, closing = false, reviewed = null;

    function selected() {
      if (!snapshot || snapshot.can_recover !== true) return null;
      if (node('keep').checked && snapshot.can_keep_data === true) return 'keep';
      if (node('restore').checked && snapshot.restart_nonce === null) return 'restore';
      return null;
    }

    function controls() {
      root.setAttribute('aria-busy', String(pending || closing));
      node('choices').disabled = pending || closing || snapshot?.can_recover !== true;
      node('refresh').disabled = pending || closing;
      node('review').disabled = pending || closing || selected() === null;
      node('commit').disabled = pending || closing;
      node('cancel').disabled = pending || closing;
    }

    function invalidate() { reviewed = null; node('confirm').hidden = true; controls(); }

    async function refresh() {
      if (pending || closing) return;
      pending = true; snapshot = null; invalidate();
      node('choices').hidden = true; node('review').hidden = true;
      node('keep').checked = false; node('restore').checked = false;
      node('status').setAttribute('role', 'status');
      node('status').textContent = 'Checking retained update files. Saved campaigns remain closed.';
      try {
        const value = await bridge.state();
        if (!value || !/^[0-9a-f]{32}$/.test(value.operation_id || '') ||
            typeof value.can_recover !== 'boolean' || typeof value.can_keep_data !== 'boolean' ||
            typeof value.previous_version !== 'string' ||
            (value.restart_nonce !== null && !/^[0-9a-f]{32}$/.test(value.restart_nonce || '')))
          throw new Error('Invalid recovery state');
        snapshot = value;
        node('status').textContent = value.message || 'Choose a data recovery option.';
        node('version').textContent = `Previous application: ${value.previous_version}`;
        node('warning').textContent = value.restart_nonce
          ? 'This retry keeps current saved data. No data checkpoint will be restored again.'
          : value.restore_warning || '';
        node('keep').disabled = value.can_keep_data !== true;
        node('keep-text').textContent = value.restart_nonce
          ? 'Retry opening the already restored application using current data'
          : 'Restore the previous application and keep current data';
        node('restore-option').hidden = value.restart_nonce !== null;
        node('choices').hidden = value.can_recover !== true;
        node('review').hidden = value.can_recover !== true;
      } catch (_error) {
        node('status').setAttribute('role', 'alert');
        node('status').textContent = 'Recovery status could not be verified. Your data stays closed. Close any other Sylvia copy using this data, then check again. Retained recovery files must be preserved.';
      } finally { pending = false; controls(); }
    }

    for (const choice of ['keep', 'restore']) node(choice).addEventListener('change', invalidate);
    node('refresh').addEventListener('click', () => { void refresh(); });
    node('review').addEventListener('click', () => {
      const choice = selected();
      if (pending || closing || !choice) return;
      reviewed = { operation: snapshot.operation_id, choice, restartNonce: snapshot.restart_nonce };
      node('confirm-text').textContent = snapshot.restart_nonce
        ? 'This retries opening the already restored application using current data. Previous restart records are retained. No data will be restored again.'
        : choice === 'restore'
        ? 'This restores the pre-update data checkpoint and loses progress saved since it. The newer data will be retained locally for recovery. Sylvia will close and restart the previous application.'
        : 'This restores the previous application while keeping your current saved data. Sylvia will close and restart that application.';
      node('confirm').hidden = false;
      node('commit').focus();
    });
    node('cancel').addEventListener('click', () => { invalidate(); node('review').focus(); });
    node('commit').addEventListener('click', async () => {
      const choice = selected();
      if (pending || closing || !reviewed || choice !== reviewed.choice ||
          snapshot.operation_id !== reviewed.operation || snapshot.restart_nonce !== reviewed.restartNonce) return;
      const request = reviewed;
      pending = true; invalidate();
      node('status').textContent = 'Preparing recovery. Waiting for the retained helper before closing Sylvia.';
      try {
        const result = await bridge.recover(request.operation, request.choice, request.restartNonce);
        if (!['closing', 'owner_closed', 'owner_unavailable'].includes(result?.status)) throw new Error('Recovery not admitted');
        closing = true;
        node('status').textContent = result.status === 'closing'
          ? 'Closing Sylvia for the recovery helper. Your selected data remains protected.'
          : 'Contact with the recovery server was lost, and handoff did not finish. Close this window and reopen recovery. No further action will be retried automatically.';
      } catch (_error) {
        snapshot = null;
        node('choices').hidden = true; node('review').hidden = true;
        node('status').setAttribute('role', 'alert');
        node('status').textContent = 'Recovery was not admitted. Check the retained status again before choosing a new recovery attempt.';
      } finally { pending = false; controls(); }
    });
    void refresh();
    return { refresh };
  }
  window.SylviaUpdateRecovery = { mount };
  document.addEventListener('DOMContentLoaded', () => {
    const invoke = window.__TAURI_INTERNALS__?.invoke;
    if (typeof invoke !== 'function') {
      document.getElementById('recovery-status').textContent = 'Open update recovery from the installed or portable Sylvia application.';
      return;
    }
    mount(document.getElementById('update-recovery'), {
      state: () => invoke('desktop_update_recovery_state'),
      recover: (operationId, choice, restartNonce) => invoke('desktop_update_recover', { operationId, choice, restartNonce }),
    });
  });
}());
