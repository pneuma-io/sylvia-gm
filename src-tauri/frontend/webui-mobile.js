/* Browser presentation only; layouts and visibility never dispatch game actions. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else document.addEventListener('DOMContentLoaded', () => api.install(root, document));
})(typeof window === 'object' ? window : globalThis, () => {
  'use strict';
  function install(win, doc) {
    const frame = doc.getElementById('session-frame');
    const bar = doc.querySelector('.webui-session-bar');
    if (!frame || !bar) return;
    const nav = doc.createElement('nav');
    nav.className = 'webui-narrow-navigation';
    nav.setAttribute('aria-label', 'Workspace');
    const buttons = [];
    for (const [pane, label] of [['chat', 'Chat'], ['panels', 'Game panels'], ['sessions', 'Sessions']]) {
      const button = doc.createElement('button');
      button.type = 'button'; button.textContent = label;
      button.className = 'btn btn-secondary btn-sm';
      button.onclick = () => selectPane(pane);
      buttons.push([pane, button]); nav.appendChild(button);
    }
    function selectPane(pane) {
      // Home owns the existing campaign and Builder lists, including draft preservation.
      if (pane === 'sessions') doc.getElementById('home-btn')?.click();
      frame.dataset.browserPane = pane;
      for (const [id, button] of buttons) button.setAttribute('aria-pressed', String(id === pane));
    }
    bar.appendChild(nav); selectPane('chat');
    doc.getElementById('home-btn')?.addEventListener('click', () => selectPane('chat'));
    // Opening from Sessions or choosing Builder shows Chat. A keyboard activation (click detail 0) would leave
    // focus on the now-hidden row, so it moves to the pressed Chat pane button; pointer clicks keep their focus.
    doc.addEventListener('click', event => {
      const opensFromSessions = frame.dataset.browserPane === 'sessions' && event.target?.closest?.(
        '[data-home-campaign-id], [data-builder-session-id]');
      if (!opensFromSessions && !event.target?.closest?.('#rail-builder-btn')) return;
      selectPane('chat');
      const focused = doc.activeElement;
      if (event.detail === 0 && (!focused || focused === doc.body || !focused.getClientRects().length))
        buttons[0][1].focus();
    });
    function viewport() {
      const height = win.visualViewport?.height || win.innerHeight;
      const top = win.visualViewport?.offsetTop || 0;
      doc.documentElement.style.setProperty('--webui-viewport-height', `${height}px`);
      doc.documentElement.style.setProperty('--webui-viewport-top', `${top}px`);
      doc.documentElement.style.setProperty('--webui-bar-height', `${bar.getBoundingClientRect().height}px`);
    }
    viewport();
    win.addEventListener('resize', viewport);
    win.visualViewport?.addEventListener('resize', viewport);
    win.visualViewport?.addEventListener('scroll', viewport);
    if (win.ResizeObserver) new win.ResizeObserver(viewport).observe(bar);
    const visibility = () => {
      viewport();
      if (win.SylviaWebUI?.isStopped()) return;
      void win.SylviaGMFrontend?.setBrowserVisibility(!doc.hidden).catch(() => {
        bar.querySelector('output').textContent = 'Connection changed. Reload to inspect saved work.';
      });
    };
    doc.addEventListener('visibilitychange', visibility);
    win.addEventListener('pageshow', (event) => { if (event.persisted) visibility(); });
    win.addEventListener('online', () => { if (!doc.hidden) visibility(); });
    return { selectPane, viewport };
  }
  return { install };
});
