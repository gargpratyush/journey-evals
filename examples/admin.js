// Meridian's own behaviour. Behavioural parameters arrive from /api/flags and are never rendered,
// so the page text a model reads describes the product and not the experiment.
(() => {
  'use strict';
  const SECTIONS = ['billing', 'review', 'scheduled', 'workspaces', 'delete', 'insights'];
  const ROUTES = {'/': 'billing', '/billing': 'billing', '/billing/review': 'review',
                  '/billing/scheduled': 'scheduled', '/workspaces': 'workspaces',
                  '/workspaces/deleted': 'workspaces', '/insights': 'insights'};
  const NAV = {billing: 'nav-billing', review: 'nav-billing', scheduled: 'nav-billing',
               workspaces: 'nav-workspaces', delete: 'nav-workspaces', insights: 'nav-insights'};
  const $ = id => document.getElementById(id);
  const money = cents => 'USD ' + (cents / 100).toLocaleString('en-US', {minimumFractionDigits: 2,
                                                                        maximumFractionDigits: 2});

  let flags = {refresh_progress: '', clip_delete: false};
  const state = {view: 'billing', preview: null, change: null, listing: null, banner: null,
                 bannerBad: false, revenue: null};

  // One persistent dispatcher. Sections are shown and hidden rather than rebuilt, so a control
  // that disappears between two states is a real transient and not an artefact of re-rendering.
  const render = () => {
    for (const name of SECTIONS) $('section-' + name).hidden = name !== state.view;
    for (const id of new Set(Object.values(NAV))) $(id).className = '';
    $(NAV[state.view]).className = 'on';

    if (state.view === 'review' && state.preview) {
      $('review-charge').textContent = money(state.preview.charge_today_cents);
      $('review-effective').textContent = longDate(state.preview.effective_date);
      $('review-notice').textContent = state.preview.notice || '';
    }
    if (state.view === 'scheduled' && state.change) {
      $('scheduled-reference').textContent = state.change.id;
      $('scheduled-effective').textContent = longDate(state.change.promised_effective_date);
    }
    if (state.view === 'workspaces') renderWorkspaces();
    if (state.view === 'delete') {
      $('delete-notice').textContent = (state.listing && state.listing.notice) || '';
      $('delete-frame').className = flags.clip_delete ? 'actions clipped' : 'actions';
    }
    if (state.view === 'insights') renderRevenue();
  };

  const longDate = iso => {
    const [y, m, d] = (iso || '').split('-').map(Number);
    const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
                    'September', 'October', 'November', 'December'];
    return y ? `${d} ${months[m - 1]} ${y}` : '';
  };

  const row = (workspace, actionLabel, handler) => {
    const item = document.createElement('li');
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = workspace.name;
    const meta = document.createElement('span');
    meta.className = 'meta';
    // The server decides how much it discloses about a workspace; the page reports what it was
    // given rather than inventing a count.
    meta.textContent = workspace.projects === undefined
      ? ''
      : `${workspace.projects} projects · ${workspace.documents} documents · ` +
        `${workspace.members} members`;
    meta.hidden = workspace.projects === undefined;
    // One text node per label: an accessible name is assembled from child nodes, and splitting it
    // would change the name every declared check matches on.
    const button = document.createElement('button');
    button.textContent = `${actionLabel} ${workspace.name}`;
    button.addEventListener('click', () => handler(workspace));
    item.append(name, meta, button);
    return item;
  };

  const renderWorkspaces = () => {
    const banner = $('workspace-state');
    banner.textContent = state.banner || '';
    banner.className = state.bannerBad ? 'state bad' : 'state';
    banner.hidden = !state.banner;
    const listing = state.listing || {active: [], deleted: []};
    $('active-list').replaceChildren(...listing.active.map(w => row(w, 'Delete', openDelete)));
    $('deleted-list').replaceChildren(...listing.deleted.map(w => row(w, 'Restore', restore)));
    $('deleted-block').hidden = listing.deleted.length === 0;
  };

  const renderRevenue = () => {
    const data = state.revenue;
    $('freshness-badge').hidden = !data;
    $('freshness-notice').hidden = !(data && data.notice);
    $('revenue-block').hidden = !data;
    if (!data) return;
    $('freshness-badge').textContent = data.badge || '';
    $('freshness-notice').textContent = data.notice || '';
    $('revenue-rows').replaceChildren(...data.regions.map(region => {
      const tr = document.createElement('tr');
      const code = document.createElement('td');
      code.textContent = region.code;
      const amount = document.createElement('td');
      amount.className = 'amount';
      amount.textContent = money(region.revenue_cents);
      tr.append(code, amount);
      return tr;
    }));
    $('revenue-total').textContent = money(data.total_cents);
  };

  const go = (path, replace) => {
    state.view = ROUTES[path] || 'billing';
    history[replace ? 'replaceState' : 'pushState']({view: state.view}, '', path);
    render();
  };

  const post = (path, body) => fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {}),
  }).then(r => r.json().then(data => ({ok: r.ok, data})));

  const loadWorkspaces = () => fetch('/api/workspaces').then(r => r.json())
    .then(data => { state.listing = data; });

  // -- billing --------------------------------------------------------------------------------
  $('to-review').addEventListener('click', async () => {
    const {data} = await post('/api/plan/preview', {});
    state.preview = data;
    go('/billing/review');
  });

  $('keep-pro').addEventListener('click', () => go('/billing'));

  $('confirm-downgrade').addEventListener('click', async () => {
    const {data} = await post('/api/plan/change', {});
    state.change = data;
    go('/billing/scheduled');
  });

  // -- workspaces -----------------------------------------------------------------------------
  const openDelete = workspace => {
    state.target = workspace;
    $('delete-name').textContent = workspace.name;
    // When the server has no impact figures the rows are absent rather than filled with a
    // placeholder: a product that never worked out what would be lost does not display the
    // question, and a placeholder would read as a deliberate disclosure of uncertainty.
    const known = workspace.projects !== undefined;
    if (known) {
      $('delete-contents').textContent = `${workspace.projects} projects · ` +
                                         `${workspace.documents} documents`;
      $('delete-members').textContent = `${workspace.members} members`;
    }
    ['delete-contents-label', 'delete-contents', 'delete-members-label', 'delete-members']
      .forEach(id => { $(id).hidden = !known; });
    $('delete-ack').checked = false;
    $('delete-status').textContent = '';
    state.view = 'delete';
    render();
  };

  $('delete-cancel').addEventListener('click', () => { state.view = 'workspaces'; render(); });

  $('delete-confirm').addEventListener('click', async () => {
    if (!$('delete-ack').checked) {
      $('delete-status').textContent = 'Confirm that you understand before deleting.';
      return;
    }
    const {ok, data} = await post('/api/workspace/delete', {id: state.target.id});
    if (!ok) { $('delete-status').textContent = data.error || 'Delete failed.'; return; }
    await loadWorkspaces();
    state.banner = `${state.target.name} deleted`;
    state.bannerBad = false;
    go('/workspaces/deleted');
  });

  const restore = async workspace => {
    const {ok, data} = await post('/api/workspace/restore', {id: workspace.id});
    await loadWorkspaces();
    state.banner = ok ? `${workspace.name} restored`
                      : `${workspace.name} could not be restored. ` +
                        (data.error || 'It is no longer available.');
    state.bannerBad = !ok;
    go('/workspaces');
  };

  // -- insights -------------------------------------------------------------------------------
  $('refresh').addEventListener('click', async () => {
    const button = $('refresh');
    button.disabled = true;
    $('refresh-status').textContent = flags.refresh_progress || '';
    try {
      const {data} = await post('/api/insights/revenue', {window: $('window').value});
      state.revenue = data;
    } finally {
      button.disabled = false;
      $('refresh-status').textContent = '';
      render();
    }
  });

  addEventListener('popstate', event => {
    state.view = (event.state && event.state.view) || ROUTES[location.pathname] || 'billing';
    render();
  });

  for (const [id, path] of [['nav-billing', '/billing'], ['nav-workspaces', '/workspaces'],
                            ['nav-insights', '/insights']]) {
    $(id).addEventListener('click', event => {
      event.preventDefault();
      if (path === '/workspaces') { state.banner = null; }
      go(path);
    });
  }

  Promise.all([
    fetch('/api/flags').then(r => r.json()).then(data => { flags = {...flags, ...data}; }),
    loadWorkspaces(),
  ]).catch(() => {}).finally(() => go(location.pathname, true));
})();
