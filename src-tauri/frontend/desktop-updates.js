/* Explicit update consent and authoritative status; never starts a check on opening Settings. */
(function () {
  'use strict';
  const phases = new Set(['idle', 'recovering', 'unavailable', 'checking', 'available', 'current',
    'downloading', 'verified', 'deferred', 'cancelled', 'interrupted', 'error',
    'preparing', 'prepared', 'closing', 'applying', 'awaiting_restart', 'recovery_required',
    'confirmed', 'rolled_back', 'rolling_back', 'rollback_ready', 'rollback_awaiting_restart']);
  const labels = {
    closing: 'Waiting for Sylvia windows and owned background work to close.',
    recovering: 'Reading previous update status…',
    idle: 'Check when you are ready.', checking: 'Checking official releases…',
    available: 'An update is available.', current: 'No newer eligible release was found.',
    downloading: 'Downloading update…', verified: 'Download verified. You can defer installation.',
    deferred: 'Update deferred. Your current app stays open.', cancelled: 'Update cancelled.',
    interrupted: 'The previous update was interrupted. Your current app is unchanged.',
    preparing: 'Preparing a safe restart…', prepared: 'Ready to update and restart.',
    applying: 'Applying update…', awaiting_restart: 'Waiting for the updated app to confirm startup…',
    recovery_required: 'The previous update needs recovery before another update can start.',
    confirmed: 'The updated app and your data opened successfully.', rolled_back: 'The previous app was restored.',
    rolling_back: 'Restoring the previous application using your selected data recovery choice.',
    rollback_ready: 'The previous application is restored and ready for its recovery restart.',
    rollback_awaiting_restart: 'Waiting for the restored app to confirm startup.',
  };

  function mount(root, bridge, install) {
    const node = (name) => root.querySelector(`[data-update-role="${name}"]`);
    let visible = false, pending = false, timer = null, epoch = 0, snapshot = null;
    const clearTimer = () => { if (timer !== null) window.clearTimeout(timer); timer = null; };

    function render(value) {
      if (!value || !phases.has(value.phase) || typeof value.current_version !== 'string') {
        throw new Error('Invalid update status');
      }
      snapshot = value;
      const busy = pending || value.busy === true;
      node('identity').textContent = `Current ${value.current_version} · ${value.channel || ''} · ${value.target || ''} · ${value.distribution || ''}`;
      node('source').textContent = value.source || 'Official Sylvia GM releases';
      node('version').textContent = typeof value.version === 'string' ? `Available ${value.version}` : '';
      node('notes').textContent = typeof value.notes === 'string' ? value.notes.slice(0, 65536) : '';
      node('notes').hidden = !node('notes').textContent;
      node('status').textContent = value.message || labels[value.phase] || 'Updates are unavailable in this build.';
      if (!value.message && value.phase === 'downloading' && value.transfer_kind === 'previous_installer')
        node('status').textContent = 'Retaining the authenticated current installer for recovery.';
      node('status').setAttribute('role', value.phase === 'error' ? 'alert' : 'status');
      root.setAttribute('aria-busy', String(busy));
      const progress = node('progress');
      progress.hidden = value.phase !== 'downloading';
      const received = Number.isSafeInteger(value.received) && value.received >= 0 ? value.received : 0;
      if (Number.isSafeInteger(value.total) && value.total > 0 && received <= value.total) {
        progress.max = value.total; progress.value = received;
        node('bytes').textContent = `${received.toLocaleString()} / ${value.total.toLocaleString()} bytes`;
      } else {
        progress.removeAttribute('value');
        node('bytes').textContent = value.phase === 'downloading' ? `${received.toLocaleString()} bytes downloaded` : '';
      }
      node('bytes').hidden = value.phase !== 'downloading';
      node('check').disabled = busy || ['unavailable', 'preparing', 'prepared', 'closing', 'applying', 'awaiting_restart', 'recovery_required', 'rolling_back', 'rollback_ready', 'rollback_awaiting_restart'].includes(value.phase);
      node('download').hidden = !['available', 'deferred'].includes(value.phase) || value.downloaded === true;
      node('download').disabled = busy;
      node('cancel').hidden = !['checking', 'downloading'].includes(value.phase);
      node('cancel').disabled = pending;
      node('defer').hidden = !['available', 'verified', 'deferred'].includes(value.phase);
      node('defer').disabled = busy;
      node('install').hidden = value.can_apply !== true || !['verified', 'deferred', 'prepared'].includes(value.phase);
      node('install').disabled = busy;
      node('not-now').disabled = busy;
      if (value.phase !== 'verified' && value.phase !== 'deferred' && value.phase !== 'prepared') node('confirm').hidden = true;
    }

    function schedule() {
      clearTimer();
      if (visible && snapshot?.busy === true && !pending) {
        timer = window.setTimeout(() => { timer = null; void request('state'); }, 500);
      }
    }

    async function request(action) {
      if (pending) return;
      const method = bridge?.[`desktop_update_${action}`];
      if (typeof method !== 'function') {
        node('status').textContent = 'In-app updating is unavailable in this build. A manual bootstrap is required.';
        node('check').disabled = true;
        return;
      }
      const identity = snapshot?.operation_id;
      if (!['state', 'check'].includes(action) && !/^[0-9a-f]{32}$/.test(identity || '')) return;
      clearTimer(); pending = true;
      const started = epoch;
      if (snapshot) render(snapshot);
      try {
        const value = await method(...(['state', 'check'].includes(action) ? [] : [identity]));
        if (started === epoch) render(value);
      } catch (_error) {
        if (started === epoch) {
          snapshot = { ...(snapshot || {}), phase: 'error', busy: false,
            current_version: snapshot?.current_version || 'unknown',
            message: 'The update operation could not complete. Saved work is kept. You can retry explicitly.' };
          render(snapshot);
        }
      } finally {
        pending = false;
        if (started === epoch && snapshot) render(snapshot);
        if (visible && started !== epoch) void request('state');
        else schedule();
      }
    }

    for (const action of ['check', 'download', 'cancel', 'defer']) {
      node(action).addEventListener('click', () => { void request(action); });
    }
    node('install').addEventListener('click', () => {
      if (!pending && snapshot?.can_apply === true) {
        node('confirm').hidden = false;
        node('confirm-install').focus();
      }
    });
    node('not-now').addEventListener('click', () => {
      if (pending || snapshot?.busy === true) return;
      node('confirm').hidden = true; node('install').focus();
    });
    node('confirm-install').addEventListener('click', async () => {
      if (pending || snapshot?.can_apply !== true || typeof install !== 'function') return;
      const identity = snapshot.operation_id;
      if (!/^[0-9a-f]{32}$/.test(identity || '')) return;
      pending = true; clearTimer(); render(snapshot);
      node('status').textContent = 'Saving drafts and preparing a safe restart. Keep this app open.';
      node('confirm-install').disabled = true;
      try { await install(identity); }
      catch (_error) { node('status').textContent = 'Restart preparation failed. Keep this app open and retry when work has finished.'; }
      finally { pending = false; node('confirm-install').disabled = false; node('confirm').hidden = true; if (visible) void request('state'); }
    });
    return {
      setVisible(value) {
        visible = value === true; epoch += 1; clearTimer();
        if (visible && !pending) void request('state');
      },
    };
  }
  window.SylviaDesktopUpdates = { mount };
}());
