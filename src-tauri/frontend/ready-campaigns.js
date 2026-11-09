/* Prepared scenarios use owner-validated copies; opening narration is a separate step. */
(function (root) {
  'use strict';

  function create(options) {
    const node = id => options.document.getElementById(id);
    const dialog = node('ready-campaign-dialog');
    let entries = [];
    let request = null;
    let busy = false;
    let warning = false;
    let customRequest = null;
    const storageKey = 'sylvia-ready-campaign-pending';
    const persist = value => {
      try {
        if (value) options.storage?.setItem(storageKey, JSON.stringify(value));
        else options.storage?.removeItem(storageKey);
      } catch (_) { /* The in-memory request still permits an exact retry. */ }
    };
    const status = text => { node('ready-campaign-status').textContent = text; };
    const selected = () => entries.find(row => row.id === node('ready-campaign-choice').value);
    const pendingCommand = value => {
      try {
        const fields = JSON.parse(value.basis);
        if (typeof value.operation_id !== 'string' || !value.operation_id ||
          !Array.isArray(fields) || fields.length !== 5 ||
          fields.some(field => typeof field !== 'string' || !field)) return null;
        const [template_id, expected_content_sha256, display_name, provider_id, model_id] = fields;
        return { operation_id: value.operation_id, template_id, expected_content_sha256,
          display_name, provider_id, model_id, model_guidance_override: value.model_guidance_override === true };
      } catch (_) { return null; }
    };
    const pendingCustomization = value => {
      if (value?.kind !== 'customize' ||
        ['operation_id', 'template_id', 'expected_content_sha256', 'title', 'draft_provider_id', 'draft_model_id']
          .some(field => typeof value[field] !== 'string' || !value[field])) return null;
      return { kind: 'customize', operation_id: value.operation_id,
        template_id: value.template_id, expected_content_sha256: value.expected_content_sha256,
        title: value.title, draft_provider_id: value.draft_provider_id, draft_model_id: value.draft_model_id };
    };
    const lock = value => {
      busy = value;
      for (const id of ['ready-campaign-choice', 'ready-campaign-name', 'ready-campaign-start',
        'ready-campaign-customize', 'ready-campaign-close']) node(id).disabled = value;
      // Reconcile an uncertain Start or customization before another command replaces it.
      if (request || customRequest) {
        for (const id of ['ready-campaign-choice', 'ready-campaign-name']) node(id).disabled = true;
      }
      if (request) node('ready-campaign-customize').disabled = true;
      if (customRequest) node('ready-campaign-start').disabled = true;
      if (!request && !customRequest && !selected()) for (const id of ['ready-campaign-start',
        'ready-campaign-customize']) node(id).disabled = true;
    };
    function render() {
      const row = selected();
      request = null;
      customRequest = null;
      warning = false;
      node('ready-campaign-start').textContent = 'Start this campaign';
      node('ready-campaign-customize').textContent = 'Customize';
      node('ready-campaign-summary').textContent = row?.summary || '';
      node('ready-campaign-name').value = row?.title || '';
      const party = node('ready-campaign-party');
      party.replaceChildren();
      for (const member of row?.party || []) {
        const item = options.document.createElement('li');
        const classes = member.classes.map(value => `${value.id} ${value.level}`).join(', ');
        item.textContent = `${member.name}, ${classes} (${member.role === 'player_character' ? 'your character' : 'companion'}). ${member.summary}`;
        if (member.profile) {
          const details = options.document.createElement('details');
          const heading = options.document.createElement('summary');
          heading.textContent = 'Character preview';
          const body = options.document.createElement('p');
          body.className = 'ready-character-profile';
          body.textContent = member.profile;
          details.append(heading, body);
          item.append(details);
        }
        party.append(item);
      }
      status('The party is prepared. Starting makes a separate editable campaign.');
    }
    async function refreshCatalog(preferred) {
      const rememberedStart = request;
      const rememberedCustomization = customRequest;
      lock(true);
      status('Loading prepared adventures.');
      try {
        entries = await options.catalog();
        const choice = node('ready-campaign-choice');
        choice.replaceChildren();
        for (const row of entries) {
          const option = options.document.createElement('option');
          option.value = row.id;
          option.textContent = row.title;
          choice.append(option);
        }
        let pending;
        try { pending = JSON.parse(options.storage?.getItem(storageKey) || 'null'); }
        catch (_) { pending = null; }
        const startRequest = pendingCommand(pending) ? pending : rememberedStart;
        const command = pendingCommand(startRequest);
        const customization = pendingCustomization(pending) || rememberedCustomization;
        const previous = entries.find(row => row.id === preferred?.template_id);
        choice.value = command?.template_id || customization?.template_id || previous?.id || entries[0]?.id || '';
        render();
        if (!entries.length) status('Prepared campaigns are unavailable. Custom campaign setup is still available.');
        if (command) {
          request = startRequest;
          node('ready-campaign-name').value = command.display_name;
          node('ready-campaign-start').textContent = 'Resume previous start';
          status(`Resume the previous start of ${command.display_name} with ${command.model_id} before starting another copy.`);
        } else if (customization) {
          customRequest = customization;
          node('ready-campaign-customize').textContent = 'Resume party customization';
          status(`Resume the previous customization of ${customization.title} before preparing another party.`);
        } else if (previous) {
          node('ready-campaign-name').value = preferred.display_name;
        }
      } catch (error) {
        entries = [];
        status(options.error('Prepared campaigns could not load', error));
      } finally { lock(false); }
    }
    async function open() {
      if (busy) return;
      dialog.showModal();
      await refreshCatalog();
    }
    async function start() {
      const row = selected();
      if (busy || customRequest) return;
      if (!request) {
        if (!row) return;
        const model = options.model();
        if (!model) {
          status('Choose a configured model before starting. You can configure a provider in Settings, then return here.');
          return;
        }
        const displayName = node('ready-campaign-name').value.trim();
        if (!displayName) { status('Enter a campaign name.'); return; }
        request = { basis: JSON.stringify([
          row.id, row.content_sha256, displayName, model.provider_id, model.model_id,
        ]), operation_id: `ready-${options.uuid()}` };
        warning = false;
      }
      request.model_guidance_override = warning || request.model_guidance_override === true;
      const command = pendingCommand(request);
      if (!command) { status('The previous start command is invalid.'); return; }
      persist(request);
      lock(true);
      status('Loading the prepared campaign locally.');
      try {
        const result = await options.start(command);
        if (result.model_guidance) {
          warning = true;
          status(options.guidance(result.model_guidance));
          node('ready-campaign-start').textContent = 'Continue with this model';
          return;
        }
        if (!result.local_ready) throw new Error('Local campaign readiness was not confirmed');
        // Keep the committed receipt separate: an interrupted opening must never create a new copy.
        dialog.close();
        persist(null);
        request = null;
        await options.onStarted(result);
      } catch (error) {
        const notCreated = error?.code === 'ready_campaign_not_created' ||
          (typeof error === 'string' && error.startsWith('ready_campaign_not_created:'));
        if (notCreated) {
          request = null;
          persist(null);
          await refreshCatalog(command);
          status(entries.length ? 'No campaign was created. Review the refreshed adventure and party before starting again.'
            : 'No campaign was created. Prepared campaigns are unavailable; reopen later or use custom setup from Home.');
        } else status(options.error('Campaign could not start; retry resumes the same copy', error));
      } finally { lock(false); }
    }
    async function customize() {
      const row = selected();
      if (busy || (!row && !customRequest) || request) return;
      if (!customRequest) {
        const model = options.customizationModel?.();
        customRequest = { kind: 'customize', operation_id: `ready-custom-${options.uuid()}`,
          template_id: row.id, expected_content_sha256: row.content_sha256, title: row.title,
          draft_provider_id: model?.provider_id || 'unconfigured.local',
          draft_model_id: model?.model_id || 'unconfigured.model' };
      }
      persist(customRequest);
      lock(true);
      status('Saving independent party copies for custom setup.');
      try {
        const { operation_id, template_id, expected_content_sha256, title } = customRequest;
        const result = await options.customize({ operation_id, template_id, expected_content_sha256 });
        await options.onCustomize({ id: template_id, title }, result, {
          operation_id: `${operation_id}-draft`, provider_id: customRequest.draft_provider_id,
          model_id: customRequest.draft_model_id,
        });
        dialog.close();
        persist(null);
        customRequest = null;
      } catch (error) {
        status(options.error('Customization could not finish; retry uses the same party copies', error));
      }
      finally { lock(false); }
    }
    node('ready-campaign-choice').addEventListener('change', render);
    node('ready-campaign-start').addEventListener('click', () => { void start(); });
    node('ready-campaign-customize').addEventListener('click', () => { void customize(); });
    node('ready-campaign-close').addEventListener('click', () => { if (!busy) dialog.close(); });
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    return { open, start, customize };
  }
  root.SylviaReadyCampaigns = { create };
  if (typeof module !== 'undefined') module.exports = { create };
})(typeof window === 'undefined' ? globalThis : window);
