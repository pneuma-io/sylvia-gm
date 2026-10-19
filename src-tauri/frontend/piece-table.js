/* Ruleset-neutral presentation of owner-projected pieces and grouped table areas. */
(function (root) {
  'use strict';
  function escape(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }

  function render(data, actions = []) {
    if (!data || data.format !== 1 || !Array.isArray(data.areas))
      return '<p class="panel-empty" role="status">Table unavailable.</p>';
    const controls = new Map(actions.map((action) => [action.id, action]));
    const statuses = (data.status || []).map((row) =>
      `<div><dt>${escape(row.label)}</dt><dd>${escape(row.value)}</dd></div>`).join('');
    const areas = data.areas.map((area) => {
      const position = ['top', 'left', 'center', 'right', 'bottom'].includes(area.position)
        ? area.position : 'center';
      const groups = (area.groups || []).map((group) => {
        let pieces;
        if (group.concealed) {
          pieces = `<p class="piece-table-concealed">${Number.isInteger(group.count)
            ? `${group.count} concealed pieces` : 'Concealed pieces'}</p>`;
        } else {
          pieces = `<ol class="piece-table-pieces">${(group.pieces || []).map((piece) => {
            const action = controls.get(piece.action_id);
            const emphasis = ['accent', 'muted'].includes(piece.emphasis) ? piece.emphasis : 'normal';
            const text = `<span>${escape(piece.label)}</span>${piece.detail
              ? `<small>${escape(piece.detail)}</small>` : ''}`;
            const content = action
              ? `<button type="button" class="piece-table-piece ruleset-panel-action" ` +
                `data-panel-action-id="${escape(action.id)}" ` +
                `aria-label="${escape(action.label)}: ${escape(piece.label)}" ` +
                `${action.available === true ? '' : 'disabled'}>${text}</button>`
              : `<span class="piece-table-piece">${text}</span>`;
            return `<li class="piece-table-${emphasis}">${content}</li>`;
          }).join('')}</ol>`;
        }
        return `<div class="piece-table-group"><h5>${escape(group.label)}</h5>${pieces}</div>`;
      }).join('');
      return `<section class="piece-table-area piece-table-${position}" ` +
        `aria-label="${escape(area.label)}"><h4>${escape(area.label)}</h4>${groups}</section>`;
    }).join('');
    return `<div class="piece-table"><header><strong>${escape(data.title)}</strong>` +
      `<dl class="piece-table-status">${statuses}</dl></header>` +
      `<div class="piece-table-areas">${areas}</div></div>`;
  }
  root.SylviaPieceTable = { render };
})(typeof window === 'undefined' ? globalThis : window);
