/* Four-sided layout of a safe owner projection. Selection is local; only explicit actions commit. */
(function (root) {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'})[char]);
  const flags = piece => new Set(piece.flags || []);
  const description = piece => [...new Set([piece.face ? root.SylviaTileFaces.describe(piece.face) : piece.label,
    ...(piece.flags || []).filter(flag => flag === 'drawn').map(() => 'Drawn tile'), piece.detail].filter(Boolean))].join('. ');
  function tiles(pieces, controls, rack = false, pond = false) {
    if (pond) {
      const visible = pieces.filter(piece => !flags(piece).has('claimed'));
      const rows = [];
      for (let index = 0; index < visible.length; index += 6)
        rows.push(tiles(visible.slice(index, index + 6), controls));
      return pieces.filter(piece => flags(piece).has('claimed')).map(piece =>
        `<span class="table-sr-only">${escape(description(piece))}. Called into a meld.</span>`).join('') +
        `<div class="table-pond-rows">${rows.join('')}</div>`;
    }
    return `<ol class="table-tiles">${pieces.map(piece => {
      const marks = flags(piece), text = description(piece);
      if (marks.has('claimed')) return `<li class="table-sr-only">${escape(text)}. Called into a meld.</li>`;
      const classes = [...marks].filter(flag => ['drawn','sideways','stacked','latest'].includes(flag)).map(flag => ` tile-${flag}`).join('');
      const face = root.SylviaTileFaces.render(piece.face, marks.has('back'));
      const action = controls.get(piece.action_id);
      const selectable = rack && action?.available === true && piece.face;
      const body = selectable ? `<button type="button" class="table-select-tile" data-table-select="${escape(piece.id)}" data-table-action="${escape(action.id)}" aria-label="Select ${escape(text)}" title="${escape(text)}" aria-pressed="false">${face}</button>` :
        `<span class="table-static-tile" tabindex="0" role="img" aria-label="${escape(marks.has('back') ? 'Face down tile' : text)}" title="${escape(marks.has('back') ? 'Face down tile' : text)}">${face || escape(piece.label)}</span>`;
      return `<li class="table-tile-slot${classes}">${marks.has('drawn') && rack ? '<span class="table-drawn-label">Drawn</span>' : ''}${body}</li>`;
    }).join('')}</ol>`;
  }
  function render(data, actions) {
    const controls = new Map(actions.map(action => [action.id, action]));
    const canDiscard = data.areas.some(area => (area.groups || []).some(group =>
      group.role === 'rack' && !group.concealed && (group.pieces || []).some(piece =>
        controls.get(piece.action_id)?.available === true)));
    const center = data.areas.find(area => area.position === 'center');
    const groups = center?.groups || [];
    const indicators = groups.filter(group => group.role === 'indicators').map(group =>
      `<div class="table-indicators"><span>${escape(group.label)}</span>${tiles(group.pieces || [], controls)}</div>`).join('');
    const status = new Map((data.status || []).map(row => [row.label, row.value]));
    const seats = data.areas.filter(area => area.position !== 'center').map(area => {
      const rack = area.groups.find(group => group.role === 'rack');
      const pond = area.groups.find(group => group.role === 'pond');
      const melds = area.groups.filter(group => group.role === 'meld');
      const concealed = rack?.concealed === true;
      const count = concealed && Number.isInteger(rack.count) ? Math.min(18, Math.max(0, rack.count)) : 0;
      const rackContent = concealed ? `<span class="table-sr-only">${count} concealed tiles</span><ol class="table-tiles" aria-hidden="true">${Array.from({length:count}, () => `<li class="table-tile-slot">${root.SylviaTileFaces.render(null, true)}</li>`).join('')}</ol>` : tiles(rack?.pieces || [], controls, true);
      return `<section class="table-seat table-seat-${escape(area.position)}${concealed ? ' table-seat-concealed' : ''}${area.active ? ' table-seat-active' : ''}" aria-label="${escape(area.label)}">` +
        `<div class="table-seat-badge"><span class="table-wind">${escape(area.badge)}</span><span><small>${escape(area.label)}</small><strong>${escape(area.score)}</strong></span>${area.active ? '<i title="Acting seat" aria-label="Acting seat"></i>' : ''}</div>` +
        `<div class="table-rack" aria-label="${escape(rack?.label || 'Hand')}">${rackContent}</div>` +
        `<div class="table-pond" aria-label="${escape(area.label)} discards">${tiles(pond?.pieces || [], controls, false, true)}</div>` +
        `<div class="table-melds">${melds.map(group => `<div class="table-meld" aria-label="${escape(group.label)}${group.source ? ', called from ' + escape(group.source) : ''}">${tiles(group.pieces || [], controls)}</div>`).join('')}</div></section>`;
    }).join('');
    const choices = groups.filter(group => group.role === 'actions' && !group.concealed).flatMap(group => group.pieces || []).filter(piece => controls.get(piece.action_id)?.available === true).map(piece =>
      `<button type="button" class="table-choice ruleset-panel-action" data-panel-action-id="${escape(piece.action_id)}" data-panel-piece-id="${escape(piece.id)}" aria-label="${escape(piece.detail || piece.label)}" title="${escape(piece.detail || piece.label)}"><span>${escape(piece.label)}</span>${(piece.faces || []).map(face => root.SylviaTileFaces.render(face)).join('')}</button>`).join('');
    const messages = groups.filter(group => group.role === 'message').flatMap(group => group.pieces || []);
    return `<div class="tile-table"><div class="table-fit"><div class="table-board" role="group" aria-label="${escape(data.title)} table">${seats}${indicators}` +
      `<div class="table-round"><strong>${escape(status.get('Round') || '')}</strong><span>${escape(status.get('Live wall') || '0')} tiles left</span><small>Honba ${escape(status.get('Honba') || '0')} · Sticks ${escape(status.get('Riichi sticks') || '0')}</small></div></div></div>` +
      `<div class="table-command-deck"><div class="table-turn" role="status">${escape(center?.label || '')}</div>` +
      `${messages.length ? `<p class="table-message">${escape(messages[0].label)}</p>` : ''}` +
      `<p class="table-selection-help">${canDiscard ? 'Select a tile, then Discard. Arrow keys move through your hand.' : choices ? 'Choose an available action.' : 'Waiting for the acting seat.'}</p>` +
      `<div class="table-context-actions" aria-label="Legal table actions">${canDiscard ? '<button type="button" class="table-discard ruleset-panel-action" data-table-discard aria-label="Discard selected tile" disabled>Discard <span>selected tile</span></button>' : ''}${choices}</div>` +
      `${messages.length > 1 ? `<details class="table-details"><summary>Table details</summary>${messages.slice(1).map(piece => `<p>${escape(piece.label)}${piece.detail ? ' · ' + escape(piece.detail) : ''}</p>`).join('')}</details>` : ''}</div></div>`;
  }
  function attach(container, changed, canSelect = () => true) {
    container.querySelectorAll('.tile-table').forEach(table => {
      const buttons = [...table.querySelectorAll('[data-table-select]')];
      const discard = table.querySelector('[data-table-discard]');
      if (!discard) return;
      function select(button) {
        if (!canSelect()) return;
        buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
        discard.dataset.panelActionId = button.dataset.tableAction;
        discard.dataset.panelPieceId = button.dataset.tableSelect;
        discard.disabled = true;
        const name = button.getAttribute('aria-label').replace(/^Select /, '');
        discard.setAttribute('aria-label', `Discard ${name}`);
        discard.querySelector('span').textContent = name.replace(/\. Drawn tile.*$/, '');
        changed();
      }
      buttons.forEach((button, index) => {
        button.addEventListener('click', () => select(button));
        button.addEventListener('keydown', event => {
          const target = event.key === 'ArrowRight' ? (index + 1) % buttons.length :
            event.key === 'ArrowLeft' ? (index + buttons.length - 1) % buttons.length :
              event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : null;
          if (target === null) return;
          event.preventDefault();buttons[target].focus();select(buttons[target]);
        });
      });
    });
  }
  root.SylviaTileTable = {render, attach};
})(typeof window === 'undefined' ? globalThis : window);
