// The synthetic application's own behaviour. Behavioural parameters arrive from /api/flags and are
// never rendered, so the page text a model sees describes the product and not the experiment.
(() => {
  'use strict';
  const SECTIONS = ['search', 'results', 'details', 'review', 'confirmation'];
  const ROUTES = {'/': 'search', '/results': 'results', '/details': 'details',
                  '/review': 'review', '/confirmation': 'confirmation'};
  const money = cents => 'USD ' + (cents / 100).toFixed(2);
  const $ = id => document.getElementById(id);

  let flags = {progress_text: '', clip_confirm: false, lose_details_on_back: false, jank_ms: 0};
  const state = {view: 'search', fare: null, passenger: '', quote: null, reference: null};

  const burn = ms => { if (!ms) return; const end = performance.now() + ms;
    while (performance.now() < end) { /* deliberate main-thread work */ } };

  // One persistent dispatcher. Sections are shown and hidden rather than rebuilt, so a control
  // that disappears between two states is a real transient and not an artefact of re-rendering.
  const render = () => {
    burn(flags.jank_ms);
    for (const name of SECTIONS) $('section-' + name).hidden = name !== state.view;
    if (state.view === 'results') {
      $('results').replaceChildren(...(state.offers || []).map(offer => {
        // A list item, so that a control's guard scope is its own row rather than the page.
        const row = document.createElement('li');
        row.className = 'row';

        const times = document.createElement('div');
        times.className = 'times';
        times.textContent = `${offer.departs} – ${offer.arrives}`;

        const leg = document.createElement('div');
        leg.className = 'leg';
        const path = document.createElement('div');
        path.className = 'path';
        path.textContent = 'Zurich ZRH \u2192 London LHR';
        const meta = document.createElement('div');
        meta.className = 'meta';
        meta.textContent = `${offer.id} · Nonstop · 1 h 30 m · ${offer.cabin}`;
        leg.append(path, meta);

        const fare = document.createElement('div');
        fare.className = 'fare';
        const amount = document.createElement('span');
        amount.className = 'amount';
        amount.textContent = money(offer.cents);
        const per = document.createElement('span');
        per.className = 'per';
        per.textContent = 'Total, 1 adult';
        fare.append(amount, per);

        // The label stays one text node: an accessible name is assembled from child nodes and
        // splitting it would change the name every declared check matches on.
        const pick = document.createElement('button');
        pick.textContent = `Select ${offer.cabin} fare`;
        pick.addEventListener('click', () => { state.fare = offer; go('/details'); });

        row.append(times, leg, fare, pick);
        return row;
      }));
    }
    if (state.view === 'details') $('passenger').value = state.passenger;
    if (state.view === 'review' && state.quote) {
      $('review-itinerary').textContent = 'Zurich to London, 20 September 2026, 1 adult.';
      $('review-passenger').textContent = 'Passenger: ' + state.passenger;
      $('selected-fare').textContent = money(state.quote.selected_cents);
      $('checkout-total').textContent = money(state.quote.checkout_cents);
      const notice = $('disclosure');
      notice.textContent = state.quote.disclosure || '';
      notice.hidden = !state.quote.disclosure;
      $('review-actions').classList.toggle('constrained', !!flags.clip_confirm);
    }
    if (state.view === 'confirmation') {
      $('confirmation-reference').textContent = 'Reference: ' + (state.reference || 'unknown');
    }
  };

  const go = (path, replace) => {
    state.view = ROUTES[path] || 'search';
    history[replace ? 'replaceState' : 'pushState']({view: state.view}, '', path);
    render();
  };

  const post = (path, body) => fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {}),
  }).then(r => r.json());

  $('search').addEventListener('click', async () => {
    const button = $('search');
    button.disabled = true;
    $('search-status').textContent = flags.progress_text || '';
    try {
      const data = await post('/api/search', {});
      state.offers = data.results || [];
      if (state.offers.length) go('/results');
    } finally {
      button.disabled = false;
      $('search-status').textContent = '';
    }
  });

  $('to-review').addEventListener('click', async () => {
    state.passenger = $('passenger').value.trim();
    if (!state.passenger) return;
    state.quote = await fetch('/api/quote').then(r => r.json());
    go('/review');
  });

  $('back-to-results').addEventListener('click', () => go('/results'));
  $('back-to-details').addEventListener('click', () => {
    if (flags.lose_details_on_back) state.passenger = '';
    go('/details');
  });

  $('confirm').addEventListener('click', async () => {
    $('confirm-status').textContent = 'Confirming your booking…';
    const response = await post('/api/book', {
      origin: 'Zurich', destination: 'London', date: '2026-09-20', fare_id: state.fare?.id || 'F001',
      cabin: state.fare?.cabin || 'Economy', passenger: state.passenger,
      acknowledged_cents: state.quote ? state.quote.checkout_cents : null,
    });
    $('confirm-status').textContent = '';
    if (response && response.id) { state.reference = response.id; go('/confirmation'); }
    else { $('confirm-status').textContent = (response && response.error) || 'Booking failed.'; }
  });

  addEventListener('popstate', event => {
    state.view = (event.state && event.state.view) || ROUTES[location.pathname] || 'search';
    if (state.view === 'details' && flags.lose_details_on_back) state.passenger = '';
    render();
  });

  fetch('/api/flags').then(r => r.json()).then(data => { flags = {...flags, ...data}; })
    .catch(() => {}).finally(() => { go(location.pathname, true); });
})();
