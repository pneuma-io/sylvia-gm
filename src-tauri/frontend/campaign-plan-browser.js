/* Public Plan browsing; the Interview owns selection, persistence and Continue. */
(() => {
  'use strict';

  function formatKey(plan) {
    const info = plan.browsing || {};
    if (info.format === 'Combat scenario') return 'combat';
    if (info.format === 'One-shot') return 'one-shot';
    return [2, 3, 6].includes(info.acts) ? String(info.acts) : '';
  }

  function matchingPlans(plans, search, format) {
    const needle = search.trim().toLocaleLowerCase();
    return plans.filter(plan => (!format || formatKey(plan) === format) &&
      `${plan.title || ''} ${plan.summary || ''}`.toLocaleLowerCase().includes(needle));
  }

  function characterLabel(plan) {
    if (plan.starting_party?.player_character?.mode !== 'fixed') return 'Compatible character';
    return plan.browsing?.protagonist ? `Play as ${plan.browsing.protagonist}` : 'Fixed protagonist';
  }

  function create(root, onSelect) {
    root.classList.add('plan-browser');
    root.innerHTML = `<div class="plan-browser-filters">
      <label>Search plans<input type="search" data-plan-search placeholder="Title or premise"></label>
      <label>Length<select data-plan-format><option value="">All lengths</option>
        <option value="combat">Combat</option><option value="one-shot">One-shot</option>
        <option value="2">Two acts</option><option value="3">Three acts</option>
        <option value="6">Six acts</option></select></label></div>
      <p class="construction-summary" data-plan-count aria-live="polite"></p>
      <div class="plan-browser-content"><div class="plan-browser-list" data-plan-list
        role="group" aria-label="Compatible Campaign Plans"></div>
      <div class="plan-browser-detail" data-plan-detail aria-live="polite"></div></div>`;
    const search = root.querySelector('[data-plan-search]');
    const format = root.querySelector('[data-plan-format]');
    const list = root.querySelector('[data-plan-list]');
    const detail = root.querySelector('[data-plan-detail]');
    const count = root.querySelector('[data-plan-count]');
    let current = { plans: [], selectedId: '', contextKey: '' };

    function text(parent, tag, value, className = '') {
      const child = document.createElement(tag);
      child.className = className;
      child.textContent = value;
      parent.appendChild(child);
      return child;
    }

    function render() {
      const shown = matchingPlans(current.plans, search.value, format.value);
      const selected = current.plans.find(plan => plan.id === current.selectedId);
      const focused = document.activeElement?.dataset.planChoice;
      list.replaceChildren();
      detail.replaceChildren();
      count.textContent = `${shown.length} of ${current.plans.length} plans`;
      for (const plan of shown) {
        const choice = text(list, 'button', '', 'plan-browser-choice');
        choice.type = 'button';
        choice.dataset.planChoice = plan.id;
        choice.setAttribute('aria-pressed', String(plan.id === current.selectedId));
        text(choice, 'span', plan.title, 'plan-browser-title');
        text(choice, 'span', [plan.browsing?.format, characterLabel(plan)].filter(Boolean).join(' · '),
          'plan-browser-meta');
        if (plan.summary) text(choice, 'span', plan.summary, 'plan-browser-pitch');
        if (plan.reusable && plan.content_sha256) {
          text(choice, 'span', `Saved copy ${plan.content_sha256.slice(0, 12)}`, 'plan-browser-meta');
        }
        choice.addEventListener('click', () => onSelect(plan.id));
        if (focused === plan.id) choice.focus();
      }
      if (!shown.length) {
        text(list, 'p', current.plans.length
          ? 'No matching plans. Try another search or length.'
          : 'No compatible plans. Go Back to change the Setting, or create a new Plan.');
      }
      if (!selected) {
        text(detail, 'p', 'Choose a plan to see its details. Continue will confirm your choice.');
        return;
      }
      text(detail, 'h3', selected.title);
      text(detail, 'p', [selected.browsing?.format,
        selected.browsing?.recommended_level ? `Level ${selected.browsing.recommended_level}` : '',
      ].filter(Boolean).join(' · '), 'plan-browser-meta');
      if (selected.summary) text(detail, 'p', selected.summary);
      text(detail, 'p', characterLabel(selected));
      if (!shown.some(plan => plan.id === selected.id)) {
        text(detail, 'p', 'Your selected plan is hidden by the current filters.', 'plan-browser-meta');
        const reveal = text(detail, 'button', 'Show selected plan', 'btn btn-secondary btn-sm');
        reveal.type = 'button';
        reveal.addEventListener('click', () => {
          search.value = ''; format.value = ''; render();
          [...list.children].find(child => child.dataset.planChoice === selected.id)?.focus();
        });
      }
    }
    search.addEventListener('input', render);
    format.addEventListener('change', render);
    return {
      update(next) {
        if (next.contextKey !== current.contextKey) { search.value = ''; format.value = ''; }
        current = { ...next };
        render();
      },
    };
  }

  const api = { create, matchingPlans, characterLabel };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.SylviaPlanBrowser = api;
})();
