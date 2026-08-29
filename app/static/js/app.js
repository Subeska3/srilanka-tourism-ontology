/* Sri Lanka Heritage Graph — front end.
   Blue always means the same thing here: the reasoner produced it. */

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  mode: 'inferred',
  queries: [],
  active: null,
  activities: new Set(),
};

/* ── Boot ──────────────────────────────────────────────────────────────── */

async function boot() {
  wireTabs();
  wireSwitch();
  wireRun();
  wireExplorer();
  wireAdder();
  await Promise.all([loadStatus(), loadQueries(), loadVocabulary()]);
}

async function getJSON(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(data.error || 'Request failed'), { data });
  return data;
}

/* ── Status and metrics ────────────────────────────────────────────────── */

async function loadStatus() {
  const pills = $('#status-pills');
  try {
    const s = await getJSON('/api/status');

    pills.innerHTML = '';
    pills.append(
      pill(s.consistent ? 'Consistent' : 'Inconsistent', s.consistent ? 'pill-ok' : 'pill-bad'),
      pill(s.engine, 'pill'),
      pill(`${s.derivedTriples.toLocaleString()} derived triples`, 'pill-ok')
    );

    setMetric('classes', s.classes);
    setMetric('props', s.objectProperties + s.dataProperties);
    setMetric('individuals', s.individuals);
    setMetric('derived', s.derivedTriples);

    $('#engine-note').textContent = `${s.assertedTriples.toLocaleString()} asserted → ${s.inferredTriples.toLocaleString()} after reasoning`;
  } catch (err) {
    pills.innerHTML = '';
    pills.append(pill('The reasoner did not start. Check the server log.', 'pill-bad'));
  }
}

function pill(text, cls) {
  const el = document.createElement('span');
  el.className = `pill ${cls}`;
  el.textContent = text;
  return el;
}

function setMetric(key, value) {
  const el = $(`.metric-value[data-key="${key}"]`);
  if (el) el.textContent = Number(value).toLocaleString();
}

/* ── Competency questions ──────────────────────────────────────────────── */

async function loadQueries() {
  state.queries = await getJSON('/api/queries');
  const list = $('#cq-list');
  list.innerHTML = '';

  state.queries.forEach((q, index) => {
    const li = document.createElement('li');
    const button = document.createElement('button');
    button.className = 'cq';
    button.innerHTML = `
      <span class="cq-id">${q.id}</span>
      <span>
        <span class="cq-text">${escapeHtml(q.question)}</span>
        <span class="cq-tag ${q.needs === 'inferred' ? 'reasoning' : ''}">${escapeHtml(q.feature)}</span>
      </span>`;
    button.addEventListener('click', () => selectQuery(index));
    li.append(button);
    list.append(li);
  });

  selectQuery(0);
}

function selectQuery(index) {
  const q = state.queries[index];
  if (!q) return;
  state.active = index;

  $$('.cq').forEach((el, i) => el.classList.toggle('is-on', i === index));
  $('#cq-title').textContent = q.question;
  $('#cq-feature').textContent = `${q.id} · ${q.feature}` +
    (q.needs === 'inferred' ? ' · needs the reasoner' : ' · answerable from asserted facts');
  $('#sparql').value = q.sparql.trim();

  if (q.needs === 'inferred') setMode('inferred');
  runQuery();
}

/* ── Knowledge-source switch (the signature control) ───────────────────── */

function wireSwitch() {
  $$('.ks-option').forEach(button => {
    button.addEventListener('click', () => {
      setMode(button.dataset.mode);
      runQuery();
    });
  });
}

function setMode(mode) {
  state.mode = mode;
  $$('.ks-option').forEach(b => b.classList.toggle('is-on', b.dataset.mode === mode));
}

/* ── Running queries ───────────────────────────────────────────────────── */

function wireRun() {
  $('#run').addEventListener('click', runQuery);
  $('#sparql').addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') runQuery();
  });
}

async function runQuery() {
  const button = $('#run');
  const results = $('#results');
  const note = $('#result-note');

  button.disabled = true;
  note.textContent = 'Running…';

  try {
    const data = await getJSON('/api/sparql', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: $('#sparql').value, mode: state.mode }),
    });
    renderTable(data);
    note.innerHTML = data.count === 0
      ? 'No rows matched.'
      : `${data.count} row${data.count === 1 ? '' : 's'}` +
        (data.inferredRows
          ? ` · <span class="lit">${data.inferredRows} produced by reasoning</span>`
          : ' · all stated directly');
  } catch (err) {
    results.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
    note.textContent = '';
  } finally {
    button.disabled = false;
  }
}

function renderTable({ columns, rows }) {
  const results = $('#results');
  if (!rows.length) {
    results.innerHTML = '<p class="empty">No rows matched. With reasoning switched off, that is often the point.</p>';
    return;
  }

  const head = columns.map(c => `<th>${escapeHtml(c)}</th>`).join('');
  const body = rows.map(row => {
    const cells = columns.map((c, i) => {
      const badge = (i === 0 && row.inferred) ? '<span class="badge">inferred</span>' : '';
      return `<td>${escapeHtml(row.cells[c])}${badge}</td>`;
    }).join('');
    return `<tr class="${row.inferred ? 'derived' : ''}">${cells}</tr>`;
  }).join('');

  results.innerHTML = `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/* ── Explorer ──────────────────────────────────────────────────────────── */

function wireExplorer() {
  let timer;
  $('#search').addEventListener('input', event => {
    clearTimeout(timer);
    timer = setTimeout(() => search(event.target.value), 220);
  });
}

async function search(term) {
  const hits = $('#hits');
  if (term.trim().length < 2) { hits.innerHTML = ''; return; }

  const { results } = await getJSON('/api/search?q=' + encodeURIComponent(term));
  if (!results.length) {
    hits.innerHTML = '<p class="empty">Nothing matched. Try a place name.</p>';
    return;
  }

  hits.innerHTML = '';
  results.forEach(hit => {
    const button = document.createElement('button');
    button.className = 'hit';
    button.innerHTML = `<span class="hit-label">${escapeHtml(hit.label)}</span>
                        <span class="hit-types">${escapeHtml(hit.types.join(' · ') || hit.short)}</span>`;
    button.addEventListener('click', () => showEntity(hit.iri));
    hits.append(button);
  });
}

async function showEntity(iri) {
  const panel = $('#entity');
  panel.innerHTML = '<p class="empty">Loading…</p>';
  const data = await getJSON('/api/entity?iri=' + encodeURIComponent(iri));

  const factRows = (facts, lit) => facts.map(f => `
    <div class="fact ${lit ? 'lit' : ''}">
      <span class="fact-p">${escapeHtml(f.predicate)}</span>
      <span>${escapeHtml(f.object)}</span>
    </div>`).join('');

  panel.innerHTML = `
    <div class="entity-head">
      <h3>${escapeHtml(data.label)}</h3>
      <span class="entity-iri">${escapeHtml(data.iri)}</span>
    </div>
    ${data.comment ? `<p class="entity-comment">${escapeHtml(data.comment)}</p>` : ''}
    <div class="fact-group">
      <p class="fact-heading">Stated in the ontology</p>
      ${factRows(data.asserted, false) || '<p class="empty">Nothing stated directly.</p>'}
    </div>
    <div class="fact-group">
      <p class="fact-heading lit">Added by the reasoner</p>
      ${factRows(data.inferred, true) || '<p class="empty">The reasoner added nothing for this entity.</p>'}
    </div>`;
}

/* ── Add an attraction ─────────────────────────────────────────────────── */

async function loadVocabulary() {
  const vocab = await getJSON('/api/vocabulary');

  fillSelect($('#f-type'), vocab.types, 'Choose a kind…');
  fillSelect($('#f-city'), vocab.cities, 'Choose a city…');
  fillSelect($('#f-access'), vocab.accessibility, 'Choose a level…');
  fillSelect($('#f-heritage'), vocab.heritage, 'None');

  const chips = $('#f-activities');
  chips.innerHTML = '';
  vocab.activities.forEach(activity => {
    const chip = document.createElement('button');
    chip.className = 'chip';
    chip.type = 'button';
    chip.textContent = activity.label;
    chip.addEventListener('click', () => {
      const on = state.activities.has(activity.iri);
      on ? state.activities.delete(activity.iri) : state.activities.add(activity.iri);
      chip.classList.toggle('is-on', !on);
    });
    chips.append(chip);
  });
}

function fillSelect(select, options, placeholder) {
  select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>` +
    options.map(o => `<option value="${escapeHtml(o.iri)}">${escapeHtml(o.label)}</option>`).join('');
}

function wireAdder() {
  $('#add').addEventListener('click', addIndividual);
  $('#reset').addEventListener('click', async () => {
    await getJSON('/api/reset', { method: 'POST' });
    $('#verdict').innerHTML = '<p class="empty">Additions cleared. The ontology is back to the submitted version.</p>';
    loadStatus();
  });
}

async function addIndividual() {
  const button = $('#add');
  $$('.field-error').forEach(el => (el.textContent = ''));
  button.disabled = true;

  const payload = {
    label: $('#f-label').value,
    type: $('#f-type').value,
    city: $('#f-city').value,
    accessibility: $('#f-access').value,
    heritage: $('#f-heritage').value,
    fee: $('#f-fee').value,
    activities: [...state.activities],
  };

  try {
    const data = await getJSON('/api/individual', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const items = data.inferredTypes.map(t => `<li class="verdict-item">${escapeHtml(t)}</li>`).join('');
    $('#verdict').innerHTML = `
      <h3 class="verdict-title">${escapeHtml(data.label)} was classified</h3>
      <p class="stage-sub">${escapeHtml(data.engine)} placed it under the classes below. None of them were typed in.</p>
      ${data.inferredProvince ? `<p class="stage-sub" style="margin-top:10px">Province derived by the property chain: <strong style="color:var(--blue-bright)">${escapeHtml(data.inferredProvince)}</strong></p>` : ''}
      <ul class="verdict-list">${items || '<li class="empty">No defined class matched. Try adding a third activity or a heritage status.</li>'}</ul>`;

    loadStatus();
  } catch (err) {
    const errors = (err.data && err.data.errors) || {};
    Object.entries(errors).forEach(([field, message]) => {
      const el = $(`.field-error[data-for="${field}"]`);
      if (el) el.textContent = message;
    });
    if (!Object.keys(errors).length) {
      $('.field-error-form').textContent = err.message;
    }
  } finally {
    button.disabled = false;
  }
}

/* ── Tabs and helpers ──────────────────────────────────────────────────── */

function wireTabs() {
  $$('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      $$('.tab').forEach(t => t.classList.toggle('is-active', t === tab));
      $$('.panel').forEach(p => p.classList.toggle('is-active', p.id === tab.dataset.panel));
    });
  });
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

boot();
