/* Browser transport. The existing frontend owns presentation and owner commands own play. */
(() => {
  'use strict';
  let stopped = false;
  let authenticationLocked = false;
  const headers = { 'X-Sylvia-WebUI': '1' };

  function unconfirmed(message) {
    return Object.assign(new Error(message), {code: 'outcome_unknown'});
  }

  async function responseValue(response) {
    if (response.status === 401) throw Object.assign(new Error(
      'WebUI session expired or signed out. Reload to sign in and inspect saved work; this request was not repeated.'
    ), { code: 'authentication_required' });
    let envelope;
    try { envelope = await response.json(); }
    catch (_) { throw unconfirmed('The local WebUI response could not be read. Reload to reconnect and check saved work; this request was not repeated.'); }
    if (!response.ok || !envelope.ok) throw Object.assign(
      new Error(envelope.error?.message || 'The command was rejected.'),
      { code: envelope.error?.code || 'command_rejected' });
    return envelope.result;
  }

  // A lost HTTP response cannot establish whether the owner committed the request.
  async function postResponse(url, options) {
    try {
      return await responseValue(await fetch(url, options));
    } catch (error) {
      if (error instanceof TypeError) throw unconfirmed(
        'The local connection was interrupted. Saved work is retained. Reload to reconnect; this request was not repeated.');
      throw error;
    }
  }

  async function rpc(method, params = {}, id = crypto.randomUUID()) {
    if (stopped) throw new Error('The local WebUI is stopped. Launch it again to continue.');
    // No automatic command retry. An uncertain receipt uses the owner's existing
    // action-receipt/reconnect paths, preserving the original operation identity.
    return postResponse('/api/command', {
      method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
      credentials: 'same-origin', body: JSON.stringify({ id, method, params }),
    });
  }

  function chooseFiles(accept, multiple = false) {
    return new Promise(resolve => {
      const input = document.createElement('input');
      input.type = 'file'; input.accept = accept; input.multiple = multiple; input.hidden = true;
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true; resolve([...input.files]); input.remove();
      };
      input.addEventListener('change', finish, { once: true });
      input.addEventListener('cancel', finish, { once: true });
      document.body.appendChild(input); input.click();
    });
  }

  const uploads = {
    install_package_file: ['package', '.sylvia-gm-package,.sylvia-gm-plan'],
    recover_campaign_file: ['recovery', '.sylvia-gm-campaign'],
    preview_character_card_file: ['character', '.json,.png'],
    ingest_builder_sources: ['builder', '.md,.txt,.json,.yaml,.yml,.pdf,.csv,.tsv,.html,.docx'],
  };
  const exports = new Set(['export_campaign_file', 'export_package_candidate_file',
    'export_campaign_plan_file', 'backup_authority']);

  async function invoke(method, params = {}) {
    if (Object.hasOwn(uploads, method)) {
      const [kind, accept] = uploads[method];
      const files = await chooseFiles(accept, kind === 'builder');
      if (!files.length) return { status: 'cancelled' };
      const body = new FormData(); body.append('params', JSON.stringify(params));
      files.forEach(file => body.append('file', file));
      return postResponse(`/api/upload/${kind}`, {
        method: 'POST', headers, credentials: 'same-origin', body,
      });
    }
    const result = await rpc(method, params);
    if (exports.has(method) && result.download) {
      const link = document.createElement('a');
      link.href = result.download; link.download = result.filename;
      document.body.appendChild(link); link.click(); link.remove();
    }
    return result;
  }

  function decorateBridge(bridge) {
    // Existing unavailable-capability presentation remains in charge of these controls.
    for (const key of ['dictation_capabilities', 'dictation_start', 'dictation_prepare',
      'dictation_stop', 'dictation_cancel', 'open_admin_audit_window', 'export_admin_audit_file',
      'preview_local_report', 'save_local_report', 'cancel_local_report']) delete bridge[key];
    return bridge;
  }

  document.addEventListener('DOMContentLoaded', () => {
    const remote = document.documentElement.dataset.webuiRemote === 'true';
    document.body.classList.add('webui');
    const style = document.createElement('style');
    style.textContent = '.webui .session-frame{height:calc(var(--webui-viewport-height,100dvh) - var(--webui-bar-height,34px));' +
      'margin-top:calc(var(--webui-bar-height,34px) + var(--webui-viewport-top,0px))}' +
      '.webui-session-bar{position:fixed;inset:var(--webui-viewport-top,0px) 0 auto;min-height:34px;z-index:1100;display:flex;' +
      'align-items:center;gap:12px;padding:0 12px;background:var(--bg-secondary);' +
      'color:var(--text-narration);font:12px sans-serif;border-bottom:1px solid var(--border-color)}' +
      '.webui-session-bar button{font:inherit}.webui-session-bar output{flex:1}';
    document.head.appendChild(style);
    const bar = document.createElement('div'); bar.className = 'webui-session-bar';
    bar.innerHTML = '<output role="status">Local WebUI · Connecting…</output>' +
      '<button class="btn btn-secondary btn-sm" type="button" data-webui-reconnect>Reload / reconnect</button>' +
      (remote ? '<button class="btn btn-secondary btn-sm" type="button" data-webui-logout>Sign out</button>' : '') +
      '<button class="btn btn-secondary btn-sm" type="button" data-webui-stop>' +
      (remote ? 'Stop WebUI for all devices' : 'Stop local WebUI') + '</button>';
    document.body.prepend(bar);
    const status = bar.querySelector('output');
    const stopButton = bar.querySelector('[data-webui-stop]');
    let recoveryDialog = null;
    let frameWasInert = false;

    // Keep every draft and unfinished form in this document. Nothing private goes into
    // URLs or browser storage, and authentication never resubmits an application command.
    function recoverAuthentication(state = 'expired') {
      if (!authenticationLocked) frameWasInert = document.getElementById('session-frame').inert;
      authenticationLocked = true;
      document.body.classList.add('webui-auth-locked');
      document.getElementById('session-frame').inert = true;
      if (!recoveryDialog) {
        recoveryDialog = document.createElement('dialog');
        recoveryDialog.className = 'webui-auth-recovery';
        recoveryDialog.setAttribute('aria-labelledby', 'webui-reauth-title');
        recoveryDialog.innerHTML = '<form><h1 id="webui-reauth-title"></h1>' +
          '<p data-recovery-note></p><p>Unsent text stays only in this tab until it can be saved. ' +
          'Keep this tab open; closing or reloading it can discard unsaved text.</p>' +
          '<label for="webui-reauth-key">Access key</label>' +
          '<input id="webui-reauth-key" type="password" autocomplete="current-password" ' +
          'required maxlength="128" spellcheck="false" autocapitalize="none">' +
          '<button type="submit">Sign in</button>' +
          '<output role="status" aria-live="polite"></output></form>';
        recoveryDialog.addEventListener('cancel', event => event.preventDefault());
        const form = recoveryDialog.querySelector('form');
        const input = form.querySelector('input');
        const button = form.querySelector('button');
        const message = form.querySelector('output');
        let authenticated = false;
        form.addEventListener('submit', async event => {
          event.preventDefault(); button.disabled = true;
          try {
            if (!authenticated) {
              const body = JSON.stringify({ key: input.value });
              input.value = '';
              const response = await fetch('/auth/login', { method: 'POST', headers: {
                ...headers, 'Content-Type': 'application/json',
              }, credentials: 'same-origin', body });
              if (!response.ok) throw new Error(response.status === 429
                ? 'Sign-in limit reached. Wait one minute before trying again.'
                : 'Sign-in failed. Check the access key and try again.');
              authenticated = true;
            }
            // Retrying text-only persistence is safe; input/actions/uploads are never repeated.
            await window.SylviaGMFrontend.flushBrowserDrafts();
            let resumeError = null;
            authenticationLocked = false;
            try { await window.SylviaGMFrontend.setBrowserVisibility(!document.hidden); }
            catch (error) {
              if (error.code === 'authentication_required') {
                authenticationLocked = true;
                throw error;
              }
              resumeError = error;
            }
            document.body.classList.remove('webui-auth-locked');
            document.getElementById('session-frame').inert = frameWasInert;
            recoveryDialog.close(); recoveryDialog.remove(); recoveryDialog = null;
            status.textContent = resumeError
              ? 'Signed in · Drafts saved · Connection needs attention. Reload to inspect saved work.'
              : 'Private WebUI · Signed in · Drafts saved · Requests were not repeated.';
          } catch (error) {
            if (error.code === 'authentication_required') authenticated = false;
            message.textContent = authenticated
              ? 'Signed in, but drafts could not be saved. Keep this tab open and try saving again.'
              : (error instanceof TypeError ? 'Connection interrupted. Unsent text is still in this tab.' : error.message);
            input.disabled = authenticated; input.required = !authenticated;
            button.textContent = authenticated ? 'Save drafts and reconnect' : 'Sign in';
            button.disabled = false;
            if (!authenticated) input.focus();
          }
        });
        document.body.appendChild(recoveryDialog);
        recoveryDialog.showModal();
      }
      const messages = {
        expired: ['Sign in to reconnect',
          'Your session expired. Sign in here to reconnect without losing your unsent text.'],
        signing_out: ['Tab locked · Signing out',
          'This tab is locked. Waiting for the server to confirm sign-out.'],
        signed_out: ['Signed out', 'This tab is locked. Sign in here to recover your unsent text.'],
        logout_unconfirmed: ['Tab locked · Sign-out unconfirmed',
          'Server-side sign-out could not be confirmed. The session may still be active. ' +
          'Sign in here to recover your text, or stop the WebUI on the host to revoke all sessions.'],
      };
      recoveryDialog.querySelector('h1').textContent = messages[state][0];
      recoveryDialog.querySelector('[data-recovery-note]').textContent = messages[state][1];
      // Do not allow reauthentication to race a logout that is still in flight.
      const pending = state === 'signing_out';
      recoveryDialog.querySelector('input').disabled = pending;
      recoveryDialog.querySelector('button').disabled = pending;
    }

    if (remote) {
      status.textContent = 'Private WebUI · Connecting…';
      const logoutButton = bar.querySelector('[data-webui-logout]');
      logoutButton.onclick = async () => {
        logoutButton.disabled = true;
        recoverAuthentication('signing_out');
        try {
          // Failed persistence cannot prevent revocation. Retain unsaved text in the locked tab.
          try { await window.SylviaGMFrontend.flushBrowserDrafts(); } catch (_) { /* Keep in memory. */ }
          try {
            await postResponse('/auth/logout', { method: 'POST', headers, credentials: 'same-origin' });
          } catch (error) {
            if (error.code !== 'authentication_required') throw error;
            // A 401 confirms this session already has no application authority.
          }
          recoverAuthentication('signed_out');
        } catch (_) { recoverAuthentication('logout_unconfirmed'); }
        finally { logoutButton.disabled = false; }
      };
    }
    bar.querySelector('[data-webui-reconnect]').onclick = async () => {
      try { await window.SylviaGMFrontend.flushBrowserDrafts(); location.reload(); }
      catch (error) {
        if (remote && error.code === 'authentication_required') recoverAuthentication();
        else status.textContent = error.message;
      }
    };
    // Each desktop-only Settings page says what the browser cannot do, under its own heading.
    for (const [id, text] of [
      ['settings-panel-accounts', 'In the browser, ChatGPT sign-in needs the Sylvia desktop app. ' +
        'Models you have already saved remain available.'],
      ['settings-panel-dictation', 'Dictation needs the Sylvia desktop app. The browser never asks for your microphone.'],
      ['settings-panel-admin', 'In the browser, the live inspector window and Audit export need the Sylvia desktop app.'],
      ['settings-panel-about', 'Updates are installed by the Sylvia desktop app, not the browser.'],
    ]) {
      const panel = document.getElementById(id);
      if (!panel) continue;
      const note = document.createElement('p');
      note.className = 'settings-note settings-browser-limit';
      note.textContent = text;
      const head = panel.querySelector('.settings-page-head');
      if (head) head.after(note); else panel.prepend(note);
    }
    stopButton.onclick = async () => {
      stopButton.disabled = true;
      try {
        await window.SylviaGMFrontend.flushBrowserDrafts();
        await postResponse('/api/stop', {
          method: 'POST', headers, credentials: 'same-origin',
        });
        stopped = true;
        status.textContent = (remote ? 'WebUI stopped for all devices' : 'Local WebUI stopped') +
          ' · Saved work retained · Launch again to continue.';
        document.getElementById('session-frame').inert = true;
      } catch (error) { status.textContent = error.message; stopButton.disabled = false; }
    };
    void rpc('campaign_index').then(() => {
      status.textContent = remote
        ? 'Private WebUI · Signed in · Closing a tab keeps saved sessions available.'
        : 'Local WebUI · This computer only · Closing a tab keeps saved sessions available.';
    }).catch(error => { status.textContent = error.message; });
  });

  window.__sylviaBrowserInvoke = invoke;
  window.SylviaWebUI = { decorateBridge, rpc, isStopped: () => stopped || authenticationLocked };
})();
