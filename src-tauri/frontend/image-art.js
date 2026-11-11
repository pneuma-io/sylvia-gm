/* Saved image art. All generation permissions, references and selections belong to the owner. */
(() => {
  'use strict';
  const labels = {character:'Complete character artwork', full_body:'Full-body reference', reference_sheet:'Multi-view reference sheet',
    portrait:'Portrait', map_underlay:'Map underlay'};
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function create({bridge, context, changed, navigateSource = null}) {
    let scopeKey = '', stamps = new Map(), generation = 0, view = null, dialog = null;
    let error = '', busy = new Set(), assets = new Map(), refreshing = null, pollTimer = null;
    let refreshSerial = 0;
    const key = () => `${context().campaign_id || ''}:${context().session_id || ''}`;
    const call = params => bridge().image_art({...params, session_id:context().session_id});
    const current = (scope, epoch) => scope === key() && epoch === generation;
    const nameFor = target => view?.targets.find(item=>item.id===target)?.name ||
      view?.selections.find(item=>item.target_id===target)?.metadata?.target_name || 'Character';
    const recovery = code => ({
      credentials_refused:'Sign in to ChatGPT in Settings, then prepare a new artwork request.',
      rate_limited:'ChatGPT image usage is currently limited. Your saved artwork is safe. Prepare a new request when usage is available.',
      interrupted:'Generation was interrupted. Your saved artwork is safe. Prepare a new request to try again.',
      provider_failure:'Generation did not finish. Your saved artwork is safe. Check your connection and prepare a new request.',
      missing_character_references:'Use Complete character artwork to prepare the required references first.',
    }[code] || String(code).replaceAll('_',' '));

    async function refresh(statusOnly = false) {
      if (!bridge()?.image_art || !context().session_id) return;
      const scope = key(), epoch = generation, serial = ++refreshSerial;
      const pending = (async () => {
        const next = await call({action:statusOnly?'status':'view'});
        if (!current(scope, epoch) || serial !== refreshSerial) return;
        const previous=JSON.stringify(view);
        view = statusOnly ? {...view,...next} : next;
        let loaded=false;
        const digests = new Set(next.selections.map(item => item.sha256));
        next.requests.slice(0,12).filter(item => item.result_sha256).forEach(item => digests.add(item.result_sha256));
        await Promise.all([...digests].filter(digest => !assets.has(digest)).map(async digest => {
          const asset = await call({action:'asset', sha256:digest});
          if (current(scope, epoch) && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(asset.data_url)) {
            assets.set(digest, asset.data_url); loaded=true;
          }
        }));
        if (current(scope, epoch) && serial === refreshSerial && (loaded || previous!==JSON.stringify(view))) { render(); changed(); }
      })();
      refreshing = pending;
      try { await pending; }
      catch (problem) { if (current(scope, epoch) && serial === refreshSerial) { error = problem.message; render(); } }
      finally {
        if (refreshing === pending) refreshing = null;
        if (current(scope,epoch) && serial === refreshSerial) {
          clearTimeout(pollTimer);
          if (view?.requests.some(item=>item.status==='pending') || view?.pending_character_art?.some(item=>['pending','advancing'].includes(item.status)))
            pollTimer=setTimeout(()=>{if(current(scope,epoch)) void refresh(true);},1000);
        }
      }
    }

    function sync(revision = '', source = 'context') {
      const next = key();
      if (scopeKey !== next) {
        clearTimeout(pollTimer);
        generation += 1; scopeKey = next; view = null; assets = new Map();
        busy = new Set(); error = ''; stamps = new Map();
        if (dialog?.open) dialog.close();
      }
      const nextStamp = `${next}:${revision}`;
      if (stamps.get(source) !== nextStamp && context().session_id && bridge()?.image_art) {
        stamps.set(source,nextStamp); void refresh();
      }
    }

    function selected(target, purpose) {
      return view?.selections.find(item => item.target_id === target && item.purpose === purpose);
    }

    function frameStyle(frame) {
      const position=`${Number(frame.x)*100}% ${Number(frame.y)*100}%`;
      return `object-position:${position};transform-origin:${position};transform:scale(${Number(frame.zoom)})`;
    }

    function portrait(target, name, className = '') {
      // Existing safe dialogue blocks retain labels. Resolve only an unambiguous public target.
      if (!target) {
        const matches=(view?.targets || []).filter(item=>item.kind==='character'&&item.name===name);
        if (matches.length!==1) return '';
        target=matches[0].id;
      }
      const item = selected(target, 'portrait'), url = item && assets.get(item.sha256);
      if (!url) return '';
      const frame = item.frame || {x:.5,y:.35,zoom:1};
      return `<span class="image-art-portrait ${escape(className)}" role="img" aria-label="${escape(name)}">` +
        `<img src="${url}" alt="" draggable="false" style="${frameStyle(frame)}"></span>`;
    }

    function underlay(map) {
      const item = map && selected(map.id, 'map_underlay');
      if (!item || !['min','max'].every(edge=>map.bounds[edge].every((value,index)=>item.metadata.bounds?.[edge]?.[index]===value))) return '';
      const target = view?.targets.find(target=>target.kind==='map'&&target.id===map.id);
      if (target?.layout_sha256 && target.layout_sha256 !== item.metadata.layout_sha256) return '';
      const url = assets.get(item.sha256);
      return url ? `<img class="image-art-underlay" src="${url}" alt="" draggable="false" aria-hidden="true">` : '';
    }

    async function perform(token, operation) {
      const scope = key(), epoch = generation;
      busy.add(token); error = ''; render();
      try { await operation(); }
      catch (problem) { if (current(scope, epoch)) error = problem.message; }
      finally { if (current(scope, epoch)) { busy.delete(token); await refresh(); } }
    }

    function requestCard(item) {
      const url = assets.get(item.result_sha256), saved = selected(item.target_id, item.purpose);
      const isBusy = busy.has(item.operation_id), status = isBusy ? 'Submitting…' : item.status;
      return `<article class="image-art-card"><h3>${escape(labels[item.purpose])}</h3>` +
        `<p>${escape(item.character_flow?.name || nameFor(item.target_id))} · ${escape(status)}</p>` +
        (item.character_flow ? `<p>Character artwork · Step ${item.character_flow.step} of up to ${item.character_flow.total}</p>` : '') +
        (url ? `<img class="image-art-preview" src="${url}" alt="${escape(labels[item.purpose])} preview">` : '') +
        (item.error ? `<p role="status">${escape(recovery(item.error))}</p>` : '') +
        `<div class="image-art-actions">` +
        (item.status === 'proposed' ? `<button type="button" class="btn btn-primary" data-art-generate="${escape(item.operation_id)}" ${isBusy ? 'disabled' : ''}>Generate</button>` : '') +
        (item.status === 'pending' || item.status === 'proposed' || isBusy ? `<button type="button" class="btn btn-secondary" data-art-cancel="${escape(item.operation_id)}">Cancel</button>` : '') +
        (item.status === 'succeeded' && saved?.operation_id !== item.operation_id ? `<button type="button" class="btn btn-primary" data-art-adopt="${escape(item.operation_id)}">Adopt</button>` : '') +
        (saved?.operation_id === item.operation_id ? '<span>Selected</span>' : '') + '</div></article>';
    }

    function selectionCard(item) {
      const url = assets.get(item.sha256);
      const frame = item.frame || {x:.5,y:.35,zoom:1};
      const controls = item.purpose === 'portrait' ? `<fieldset data-art-frame="${escape(item.target_id)}" data-art-sha="${item.sha256}"><legend>Portrait framing</legend>` +
        [['x','Horizontal position',0,1,.01],['y','Vertical position',0,1,.01],['zoom','Zoom',1,4,.05]].map(([name,label,min,max,step]) =>
          `<label>${label}<input type="range" name="${name}" min="${min}" max="${max}" step="${step}" value="${frame[name]}"></label>`).join('') +
        '<p>Zoom in, then position the face. Save framing applies this crop everywhere.</p><button type="button" class="btn btn-secondary" data-art-save-frame>Save framing</button></fieldset>' : '';
      return `<article class="image-art-card"><h3>${escape(nameFor(item.target_id))} · ${escape(labels[item.purpose])}</h3>` +
        (url ? `<div class="image-art-selected-preview">${item.purpose === 'portrait' ? portrait(item.target_id,item.target_id) : `<img class="image-art-preview" src="${url}" alt="${escape(labels[item.purpose])}">`}</div>` : '<p>Image unavailable.</p>') + controls +
        `<button type="button" class="btn btn-secondary" data-art-edit="${item.sha256}" data-art-target="${escape(item.target_id)}" data-art-purpose="${item.purpose}">Edit image</button></article>`;
    }

    function render() {
      if (!dialog?.open) return;
      const draft = dialog.querySelector('[data-art-form]');
      const values = draft ? Object.fromEntries(new FormData(draft)) : {};
      const active = dialog.contains(document.activeElement) ? document.activeElement : null;
      const focusName = active?.name;
      const caret = typeof active?.selectionStart === 'number' ? [active.selectionStart,active.selectionEnd] : null;
      const frames = [...dialog.querySelectorAll('[data-art-frame]')].map(fieldset => ({
        target:fieldset.dataset.artFrame, sha:fieldset.dataset.artSha,
        values:Object.fromEntries([...fieldset.querySelectorAll('input')].map(input=>[input.name,input.value]))
      }));
      const settings = view?.settings || {mode:'review'};
      const targets = view?.targets || [];
      dialog.innerHTML = `<header><h2 id="image-art-title">Images</h2><button type="button" class="btn btn-secondary" data-art-close aria-label="Close images">Close</button></header>` +
        `<section class="image-art-settings"><label>Image generation <select data-art-mode aria-label="Image generation mode">` +
        ['off','review','auto'].map(mode => `<option value="${mode}" ${settings.mode===mode?'selected':''}>${mode[0].toUpperCase()+mode.slice(1)}</option>`).join('') +
        `</select></label><p>ChatGPT subscription · Uses your usage limits and eligible credits. Auto lets Sylvia generate and use images. Review asks before generation and adoption.</p></section>` +
        `<p class="image-art-error" role="status">${escape(error)}</p>` +
        ((view?.pending_character_art || []).length ? `<section><h2>Artwork from Builder</h2>${view.pending_character_art.map((item,index)=>
          `<p>${escape(item.name)} · ${escape(item.status==='pending'?'Generating':item.status==='proposed'||item.status==='review'?'Waiting for approval':item.status.replaceAll('_',' '))} ` +
          (navigateSource&&!item.source_unavailable?`<button type="button" class="btn btn-secondary" data-art-source="${index}">Open Builder artwork</button>`:'')+'</p>').join('')}</section>`:'') +
        (context().session_id ? `<form data-art-form><div class="image-art-fields"><label>Character or map ` +
          `<select name="target_id">${targets.map(target => `<option value="${escape(target.id)}">${escape(target.name)} (${target.kind})</option>`).join('')}${!context().campaign_id?'<option value="">New character</option>':''}</select>` +
          `</label><label data-art-name>Character name <input name="name" maxlength="160" placeholder="Name of your character or companion">` +
          `</label><label>Image type <select name="purpose">${Object.entries(labels).filter(([purpose]) => purpose !== 'map_underlay' || targets.some(target=>target.kind==='map')).map(([purpose,label]) => `<option value="${purpose}">${label}</option>`).join('')}</select></label></div>` +
          `<label>Established appearance <textarea name="appearance" rows="2" maxlength="16000" placeholder="Identity, face, hair, physique, outfit and equipment"></textarea></label>` +
          `<label>Art brief <textarea name="brief" rows="3" maxlength="12000" placeholder="Optional visual style, or describe the changes you want"></textarea></label>` +
          `<p data-art-workflow>Creates the missing full-body reference, other views and portrait in order. Review asks before each generation; Auto continues for you. Existing artwork is reused.</p>` +
          `<input type="hidden" name="edit_sha256"><p data-art-editing hidden>Editing the selected image. <button type="button" class="btn btn-secondary" data-art-new>Start a new image</button></p><button type="submit" class="btn btn-primary" ${busy.has('request')||settings.mode==='off'?'disabled':''}>${settings.mode==='auto'?'Generate and use':'Prepare request'}</button></form>` +
          `<h2>Recent requests</h2><div class="image-art-grid">${(view?.requests || []).filter((item,index,rows)=>!item.character_flow||rows.findIndex(other=>other.character_flow?.id===item.character_flow.id)===index).slice(0,12).map(requestCard).join('') || '<p>No image requests yet. Sylvia can prepare these as it builds or runs your game.</p>'}</div>` +
          `<h2>Saved art</h2><div class="image-art-grid">${(view?.selections || []).map(selectionCard).join('') || '<p>No selected art yet.</p>'}</div>` : '<p>Open a GM or Builder chat to prepare images.</p>');
      const nextForm = dialog.querySelector('[data-art-form]');
      if (nextForm) for (const [name,value] of Object.entries(values)) {
        if (nextForm.elements.namedItem(name)) nextForm.elements.namedItem(name).value = value;
      }
      updateForm();
      for (const saved of frames) {
        const fieldset = [...dialog.querySelectorAll('[data-art-frame]')].find(item=>item.dataset.artFrame===saved.target&&item.dataset.artSha===saved.sha);
        if (fieldset) {
          for (const input of fieldset.querySelectorAll('input')) input.value=saved.values[input.name];
          updateFramePreview(fieldset);
        }
      }
      const focus = focusName && nextForm?.elements.namedItem(focusName);
      if (focus) { focus.focus({preventScroll:true}); if (caret) focus.setSelectionRange(...caret); }
    }

    function updateFramePreview(fieldset) {
      const preview=fieldset?.closest('.image-art-card')?.querySelector('.image-art-portrait img');
      if (!preview) return;
      const frame=Object.fromEntries([...fieldset.querySelectorAll('input')].map(input=>[input.name,Number(input.value)]));
      preview.style.cssText=frameStyle(frame);
    }

    function updateForm() {
      const form = dialog?.querySelector('[data-art-form]');
      if (!form) return;
      const kind = view?.targets.find(target=>target.id===form.elements.target_id.value)?.kind;
      for (const option of form.elements.purpose.options)
        option.disabled = kind ? (option.value==='map_underlay') !== (kind==='map') : option.value!=='character';
      if (form.elements.purpose.selectedOptions[0]?.disabled)
        form.elements.purpose.value = [...form.elements.purpose.options].find(option=>!option.disabled)?.value || '';
      dialog.querySelector('[data-art-editing]').hidden = !form.elements.edit_sha256.value;
      dialog.querySelector('[data-art-name]').hidden = !!form.elements.target_id.value;
      form.elements.name.required = !form.elements.target_id.value;
      const complete=form.elements.purpose.value==='character';
      const established=view?.targets.find(target=>target.id===form.elements.target_id.value)?.appearance;
      if (complete&&!form.elements.appearance.value&&established) form.elements.appearance.value=established;
      dialog.querySelector('[data-art-workflow]').hidden=!complete;
      form.elements.appearance.required=complete&&!established&&!view?.selections.some(item=>item.target_id===form.elements.target_id.value&&item.metadata?.appearance);
      form.querySelector('[type=submit]').textContent=complete
        ? (view?.settings.mode==='auto'?'Create artwork':'Prepare artwork')
        : (view?.settings.mode==='auto'?'Generate and use':'Prepare request');
    }

    async function open(options = {}) {
      if (!bridge()?.image_art) return;
      sync();
      if (!dialog) {
        dialog = document.createElement('dialog'); dialog.className = 'image-art-dialog';
        dialog.setAttribute('aria-labelledby','image-art-title'); document.body.appendChild(dialog);
        dialog.addEventListener('click', event => {
          const button = event.target.closest('button'); if (!button) return;
          if (button.hasAttribute('data-art-close')) return dialog.close();
          if (button.hasAttribute('data-art-source')) {
            const source=view.pending_character_art[Number(button.dataset.artSource)];
            dialog.close(); if(navigateSource) void navigateSource(source); return;
          }
          if (button.hasAttribute('data-art-new')) {
            dialog.querySelector('[data-art-form]').elements.edit_sha256.value=''; updateForm(); return;
          }
          const id = button.dataset.artGenerate || button.dataset.artAdopt || button.dataset.artCancel;
          if (button.dataset.artGenerate) void perform(id, () => call({action:'generate',operation_id:id}));
          if (button.dataset.artCancel) void perform(`cancel:${id}`, () => call({action:'cancel',operation_id:id}));
          if (button.dataset.artAdopt) {
            const request = view.requests.find(item=>item.operation_id===id);
            void perform(id, () => call({action:'adopt',operation_id:id,
              expected_sha256:selected(request.target_id,request.purpose)?.sha256 || null}));
          }
          if (button.dataset.artEdit) {
            const form = dialog.querySelector('[data-art-form]');
            form.elements.target_id.value=button.dataset.artTarget;
            form.elements.purpose.value=button.dataset.artPurpose==='full_body'?'character':button.dataset.artPurpose;
            form.elements.edit_sha256.value=button.dataset.artEdit;
            form.elements.appearance.value=selected(button.dataset.artTarget,button.dataset.artPurpose)?.metadata?.appearance || '';
            updateForm();
            form.elements.brief.focus();
          }
          if (button.hasAttribute('data-art-save-frame')) {
            const fieldset=button.closest('fieldset'), frame={};
            fieldset.querySelectorAll('input').forEach(input=>frame[input.name]=Number(input.value));
            void perform('frame',()=>call({action:'frame',target_id:fieldset.dataset.artFrame,
              sha256:fieldset.dataset.artSha,frame}));
          }
        });
        dialog.addEventListener('change', event => {
          if (event.target.matches('[name="target_id"],[name="purpose"]')) {
            if (event.target.name==='target_id') {
              const form=dialog.querySelector('[data-art-form]');
              form.elements.appearance.value=view?.selections.find(item=>item.target_id===form.elements.target_id.value&&item.metadata?.appearance)?.metadata.appearance || '';
            }
            dialog.querySelector('[data-art-form]').elements.edit_sha256.value=''; updateForm();
          }
          if (event.target.matches('[data-art-mode]')) void perform('mode',async()=>{
            const settings=await call({action:'save_settings',mode:event.target.value});
            if (!view) view={settings,selections:[],requests:[],targets:[]}; else view.settings=settings;
          });
        });
        dialog.addEventListener('input', event => {
          const fieldset=event.target.closest('[data-art-frame]');
          if (fieldset) updateFramePreview(fieldset);
        });
        dialog.addEventListener('submit', event => {
          if (!event.target.matches('[data-art-form]')) return;
          event.preventDefault(); const data=Object.fromEntries(new FormData(event.target));
          if (!data.edit_sha256) delete data.edit_sha256;
          if (!data.target_id) delete data.target_id;
          if (data.target_id || !data.name) delete data.name;
          data.brief=data.brief.trim()||'Create artwork that fits the established character and campaign.';
          void perform('request',async()=>{
            await call({action:'request',operation_id:crypto.randomUUID(),...data});
            const form=dialog.querySelector('[data-art-form]');
            if (form) form.elements.edit_sha256.value='';
          });
        });
      }
      if (!dialog.open) dialog.showModal(); render();
      if (context().session_id) await refresh();
      else {
        const scope=key(),epoch=generation,settings=await call({action:'settings'});
        if (current(scope,epoch)) { view={settings,targets:[],requests:[],selections:[]}; render(); }
      }
      if (options.target_id) {
        const form=dialog.querySelector('[data-art-form]');
        if (form) {
          form.elements.target_id.value=options.target_id;
          form.elements.purpose.value='character';
          form.elements.appearance.value=selected(options.target_id,'full_body')?.metadata?.appearance || '';
          updateForm();
          form.elements.appearance.focus();
        }
      }
    }
    const revision = () => JSON.stringify((view?.selections || []).map(item=>[
      item.target_id,item.purpose,item.sha256,item.frame
    ]));
    const button = (target,name) => !window.__sylviaPanelContext && context().session_id
      ? `<button type="button" class="btn btn-secondary btn-sm" data-character-art="${escape(target)}" data-art-name="${escape(name)}">Artwork</button>` : '';
    return {open,sync,refresh,portrait,underlay,revision,button};
  }
  window.SylviaImageArt={create};
})();
