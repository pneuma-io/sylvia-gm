(() => {
  'use strict';

  const invoke = window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke;
  const listen = window.__TAURI__ && window.__TAURI__.event && window.__TAURI__.event.listen;
  const records = [];
  let enabled = false;
  let paused = false;
  let polling = false;
  let nextIndex = 0;
  let pageEnd = null;
  let totalCount = 0;

  const $ = (id) => document.getElementById(id);
  const text = (value) => value == null ? '' : String(value);
  const lowerJson = (value) => text(JSON.stringify(value)).toLowerCase();

  function clearRecords() {
    records.length = 0;
    nextIndex = 0;
    pageEnd = null;
    $('audit-records').replaceChildren();
    render();
  }

  function privateReasoning(record) {
    const haystack = [record.kind, record.input_stage, record.output_stage, record.role]
      .map((value) => text(value).toLowerCase()).join(' ');
    return haystack.includes('reasoning') || haystack.includes('private');
  }

  function firstValue(record, names) {
    for (const name of names) {
      if (record && record[name] != null) return record[name];
    }
    return null;
  }

  function recordMeta(record) {
    const values = [
      firstValue(record, ['input_stage', 'output_stage', 'stage']),
      firstValue(record, ['exchange_id']),
      firstValue(record, ['session_id']),
      firstValue(record, ['attempt_id']),
      firstValue(record, ['turn_id', 'TURN']),
      firstValue(record, ['action_id']),
      firstValue(record, ['discovery_id', 'mcp_discovery_id']),
    ].filter((value) => value != null && text(value));
    return values.length ? values.map(text).join(' · ') : 'No correlation fields supplied';
  }

  function makeRecord(record, index) {
    const details = document.createElement('details');
    details.className = 'record';
    const summary = document.createElement('summary');
    const kind = document.createElement('span');
    kind.className = 'record-kind';
    kind.textContent = text(record && record.kind || 'audit_record');
    if (privateReasoning(record)) {
      const badge = document.createElement('span');
      badge.className = 'private-badge';
      badge.textContent = 'private reasoning';
      kind.appendChild(badge);
    }
    const meta = document.createElement('span');
    meta.className = 'record-meta';
    meta.textContent = recordMeta(record);
    const ordinal = document.createElement('span');
    ordinal.className = 'record-index';
    ordinal.textContent = `#${index + 1}`;
    summary.append(kind, meta, ordinal);
    details.append(summary);
    details.addEventListener('toggle', () => {
      if (!details.open || details.querySelector('pre')) return;
      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify(record, null, 2);
      details.append(pre);
    }, {once: true});
    return details;
  }

  function render() {
    const query = $('audit-search').value.trim().toLowerCase();
    const visible = query
      ? records.map((record, index) => ({record, index}))
        .filter(({record}) => lowerJson(record).includes(query))
      : records.map((record, index) => ({record, index}));
    const container = $('audit-records');
    const fragment = document.createDocumentFragment();
    if (!visible.length) {
      fragment.append($('empty-template').content.cloneNode(true));
    } else {
      for (const {record, index} of visible) {
        fragment.append(makeRecord(record, nextIndex - records.length + index));
      }
    }
    container.replaceChildren(fragment);
    $('record-count').textContent = query
      ? `${visible.length} of ${records.length} records`
      : `${records.length} records`;
    $('older-btn').disabled = polling || nextIndex <= records.length;
    $('newer-btn').disabled = polling || nextIndex >= totalCount;
    $('latest-btn').disabled = polling || pageEnd === null;
    if ($('auto-follow').checked && visible.length) {
      container.lastElementChild?.scrollIntoView({block: 'end'});
    }
  }

  async function poll(force = false) {
    if (!invoke || polling) return;
    polling = true;
    try {
      const status = await invoke('admin_full_audit_status') || {};
      const nextEnabled = status.enabled === true;
      const traceCount = Number.isSafeInteger(status.trace_count) && status.trace_count >= 0
        ? status.trace_count : 0;
      totalCount = traceCount;
      if (!nextEnabled) {
        enabled = false;
        if (records.length) clearRecords();
        $('audit-status').textContent = 'Full Audit is disabled. Enable it in Settings → Admin.';
        $('audit-status').classList.add('disabled');
        return;
      }
      enabled = true;
      $('audit-status').classList.remove('disabled');
      if (traceCount < nextIndex) clearRecords();
      if (paused && !force) {
        $('audit-status').textContent = `View paused · ${traceCount} records retained by Sylvia`;
        return;
      }
      const end = pageEnd === null ? traceCount : Math.min(pageEnd, traceCount);
      const start = Math.max(0, end - 50);
      if (nextIndex !== end || records.length !== end - start) {
        const page = [];
        for (let index = start; index < end; index += 1) {
          const projection = await invoke('admin_full_audit_record', {index}) || {};
          page.push(projection.trace != null ? projection.trace : {
            kind: 'audit_record_export_only',
            index,
            reason: text(projection.unavailable_reason || 'live_view_unavailable'),
            bytes: projection.bytes == null ? null : Number(projection.bytes),
          });
        }
        records.splice(0, records.length, ...page);
        nextIndex = end;
        render();
      }
      $('audit-status').textContent =
        `Records ${records.length ? start + 1 : 0}–${end} of ${traceCount} · Export all from Settings → Admin`;
    } catch (error) {
      $('audit-status').textContent = 'Full Audit is temporarily unavailable.';
      $('audit-status').classList.add('disabled');
    } finally {
      polling = false;
      $('older-btn').disabled = nextIndex <= records.length;
      $('newer-btn').disabled = nextIndex >= totalCount;
      $('latest-btn').disabled = pageEnd === null;
    }
  }

  $('audit-search').addEventListener('input', render);
  function showPage(end) {
    if (polling) return;
    pageEnd = end;
    $('auto-follow').checked = end === null;
    void poll(true);
  }
  $('older-btn').addEventListener('click', () => showPage(Math.max(1, nextIndex - records.length)));
  $('newer-btn').addEventListener('click', () => showPage(Math.min(totalCount, nextIndex + 50)));
  $('latest-btn').addEventListener('click', () => showPage(null));
  $('pause-btn').addEventListener('click', () => {
    paused = !paused;
    $('pause-btn').textContent = paused ? 'Resume view' : 'Pause view';
    if (!paused) render();
  });
  $('auto-follow').addEventListener('change', () => {
    if ($('auto-follow').checked) showPage(null);
    else pageEnd = nextIndex;
  });

  if (listen) {
    void listen('admin-full-audit-changed', (event) => {
      if (!event.payload || event.payload.enabled !== true) {
        enabled = false;
        clearRecords();
        $('audit-status').textContent = 'Full Audit is disabled. Enable it in Settings → Admin.';
        $('audit-status').classList.add('disabled');
      } else {
        void poll();
      }
    });
  }
  render();
  void poll();
  window.setInterval(() => { void poll(); }, 750);
})();
