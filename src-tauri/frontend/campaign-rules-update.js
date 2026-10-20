/* Campaign-scoped review. The owner decides eligibility and compatibility. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.SylviaCampaignRulesUpdate = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  function mount({ root, bridge, campaign, adopted }) {
    let reviewed = null, operationId = null, generation = 0;
    const doc = root.ownerDocument;
    root.innerHTML = '<h3>Update campaign rules</h3>' +
      '<p>Choose an installed snapshot, review its changes, then adopt it for this campaign. ' +
      'Use Stop Session before adopting. Saved progress stays unchanged.</p>' +
      '<button type="button" class="btn btn-secondary" data-rules="load">Choose a version</button>' +
      '<label hidden data-rules="selection">Installed snapshot ' +
      '<select data-rules="versions" aria-label="Installed rules snapshot"></select></label>' +
      '<button type="button" class="btn btn-secondary" data-rules="preview" hidden>Preview changes</button>' +
      '<div data-rules="review" hidden></div>' +
      '<button type="button" class="btn btn-primary" data-rules="adopt" hidden>Adopt reviewed version</button>' +
      '<button type="button" class="btn btn-secondary" data-rules="cancel" hidden>Cancel review</button>' +
      '<p data-rules="status" role="status" aria-live="polite"></p>';
    const el = name => root.querySelector(`[data-rules="${name}"]`);
    const status = text => { el('status').textContent = text; };
    const text = (parent, tag, value) => {
      const node = doc.createElement(tag); node.textContent = value; parent.append(node); return node;
    };
    function clear() {
      reviewed = null; operationId = null;
      ['review', 'adopt', 'cancel'].forEach(name => { el(name).hidden = true; });
      el('review').replaceChildren();
    }
    function context() {
      const id = campaign();
      if (!id) throw new Error('Select an active campaign first.');
      return id;
    }
    function current(id, request) { return campaign() === id && request === generation; }
    el('load').addEventListener('click', async () => {
      const request = ++generation; clear();
      el('selection').hidden = true; el('preview').hidden = true;
      try {
        const id = context(); status('Reading installed versions…');
        const result = await bridge().campaign_rules_update_options(id);
        if (!current(id, request)) return;
        el('versions').replaceChildren();
        (result.candidates || []).forEach(item => {
          const option = text(el('versions'), 'option', `${item.label} · ${item.closure_sha256.slice(0, 12)}`);
          option.value = item.candidate_id;
        });
        const available = Boolean(result.candidates?.length);
        el('selection').hidden = !available; el('preview').hidden = !available;
        status(`Current: ${result.current_label}. ` + (available
          ? 'Select a snapshot to check against your saved campaign.'
          : 'No other eligible snapshot is installed for this Ruleset and Setting.'));
      } catch (error) { if (request === generation) status(String(error.message || error)); }
    });
    el('versions').addEventListener('change', () => { ++generation; clear(); status('Preview this selection before adopting.'); });
    el('preview').addEventListener('click', async () => {
      const request = ++generation; clear();
      try {
        const id = context(), selected = el('versions').value;
        status('Checking compatibility…');
        const result = await bridge().preview_campaign_rules_update(id, selected);
        if (!current(id, request) || el('versions').value !== selected) return;
        reviewed = result; operationId = `rules-update-${crypto.randomUUID()}`;
        const box = el('review'); box.hidden = false;
        text(box, 'h4', `${result.campaign_name}: ${result.current_label} → ${result.selected_label}`);
        for (const change of result.changes || []) {
          text(box, 'p', `${change.package_id}: ${change.from_version} → ${change.to_version}`);
          const details = doc.createElement('details'); box.append(details);
          text(details, 'summary', `${change.changed_files.length} changed file${change.changed_files.length === 1 ? '' : 's'}`);
          const list = doc.createElement('ul'); details.append(list);
          change.changed_files.forEach(path => text(list, 'li', path));
        }
        if (result.added_panels?.length) text(box, 'p', `New sidebar panels: ${result.added_panels.join(', ')}.`);
        text(box, 'p', 'Your progress, existing windows, arrangement and applicable views stay in place.');
        text(box, 'p', result.recovery);
        if (result.conflicts?.length) {
          text(box, 'h4', 'Update blocked');
          const list = doc.createElement('ul'); box.append(list);
          result.conflicts.forEach(conflict => text(list, 'li', `${conflict.message} (${conflict.path})`));
        }
        el('adopt').hidden = !result.compatible;
        el('cancel').hidden = false;
        status(result.compatible ? 'Compatible with unchanged saved values. Review, then choose Adopt.'
          : 'No changes applied. Resolve these conflicts before previewing again.');
      } catch (error) { if (request === generation) status(String(error.message || error)); }
    });
    el('cancel').addEventListener('click', () => { ++generation; clear(); status('Review cancelled. No changes applied.'); });
    el('adopt').addEventListener('click', async () => {
      const preview = reviewed, request = generation;
      if (!preview || !preview.compatible || campaign() !== preview.campaign_id) {
        clear(); status('Campaign selection changed. Choose a version and preview again.'); return;
      }
      ['load', 'versions', 'preview', 'adopt', 'cancel'].forEach(name => { el(name).disabled = true; });
      try {
        const receipt = await bridge().adopt_campaign_rules_update({
          campaign_id: preview.campaign_id, candidate_id: preview.candidate_id,
          activation_id: operationId, expected_revision: preview.expected_revision,
          expected_lock_id: preview.expected_lock_id, confirmation: preview.confirmation,
        });
        if (!current(preview.campaign_id, request)) return;
        clear();
        try {
          await adopted(preview.campaign_id);
          if (campaign() === preview.campaign_id)
            status(`Rules updated. Campaign revision ${receipt.resulting_revision}. Previous versions remain retained.`);
        } catch (_) {
          if (campaign() === preview.campaign_id)
            status('Rules updated. Reopen this campaign to refresh its display. Previous versions remain retained.');
        }
      } catch (error) {
        // Keep the exact operation identity for an acknowledgement-loss retry.
        if (current(preview.campaign_id, request))
          status(`${error.message || error} Review again if the campaign changed; retry uses the same adoption request.`);
      } finally { ['load', 'versions', 'preview', 'adopt', 'cancel'].forEach(name => { el(name).disabled = false; }); }
    });
    return { reset() { ++generation; clear(); el('selection').hidden = true; el('preview').hidden = true; status('Choose a version for the selected campaign.'); } };
  }
  return { mount };
});
