// House AU — Estimates surface.
//
// Self-contained: doesn't share api.js's chamber-Hansard plumbing
// because the data shape and search idiom are different. Reads
// estimates-archives.json (manifest) plus per-round shards on demand.
// Each row is a department-level section (e.g. "Department of the
// Prime Minister and Cabinet" for one date), not a single speaker turn.

import { formatDate, snippetHtml, escapeHtml, buildSearchRegex } from './format.js?v=8';

const MANIFEST_URL = './estimates-archives.json';

// ---------- state ----------
const state = {
  term:           '',
  roundId:        '',          // '' = all rounds (default — most recent first)
  committeeSlug: '',           // '' = all committees
  manifest:       null,
  searchToken:    0,
};

// ---------- DOM ----------
const $form        = document.getElementById('est-form');
const $q           = document.getElementById('est-q');
const $round       = document.getElementById('est-round');
const $committee   = document.getElementById('est-committee');
const $status      = document.getElementById('est-status');
const $results     = document.getElementById('est-results');
const $stamp       = document.getElementById('index-stamp');

// ---------- shard cache ----------
const _shardCache = new Map();
function loadShard(spec) {
  if (!_shardCache.has(spec.url)) {
    _shardCache.set(spec.url, fetch(`./${spec.url}`).then(async (r) => {
      if (!r.ok) throw new Error(`shard ${spec.url} ${r.status}`);
      return r.json();
    }));
  }
  return _shardCache.get(spec.url);
}

// ---------- manifest ----------
async function getManifest() {
  if (state.manifest) return state.manifest;
  const r = await fetch(MANIFEST_URL);
  if (!r.ok) throw new Error(`manifest ${r.status}`);
  state.manifest = await r.json();
  return state.manifest;
}

// ---------- search ----------
function termMatcher(term) {
  if (!term) return () => true;
  const re = buildSearchRegex(term);
  return (text) => re.test(text || '');
}

async function runSearch() {
  const myToken = ++state.searchToken;
  $results.innerHTML = '';
  setStatus('Searching…');
  $form.classList.add('is-loading');

  try {
    const m = await getManifest();
    const rounds = m.rounds || [];
    const targetRounds = state.roundId
      ? rounds.filter((r) => r.id === state.roundId)
      : rounds;
    if (!targetRounds.length) {
      setStatus('No rounds available.', true);
      return;
    }

    const matchTerm = termMatcher(state.term);
    const hits = [];

    for (const r of targetRounds) {
      const rows = await loadShard(r);
      if (myToken !== state.searchToken) return;            // user moved on
      for (const row of rows) {
        if (state.committeeSlug && row.committee_slug !== state.committeeSlug) continue;
        // Skip portfolio_header rows when filter empty — they're navigation
        // not substance. Surface them only if the search term actually hits.
        if (row.section_kind === 'portfolio_header' && !state.term) continue;
        if (!matchTerm(row.fullText)) continue;
        hits.push(row);
      }
    }

    hits.sort((a, b) =>
      (b.date || '').localeCompare(a.date || '') ||
      (a.committee_slug || '').localeCompare(b.committee_slug || '')
    );

    if (!hits.length) {
      setStatus(state.term
        ? `No matches for ${JSON.stringify(state.term)}${describeFilters()}.`
        : `No sections found${describeFilters()}.`);
      return;
    }
    renderHits(hits);
    setStatus(`${hits.length.toLocaleString('en-AU')} section${hits.length === 1 ? '' : 's'}${describeFilters()}.`);
  } finally {
    // Only clear the loading state if we're still the latest search —
    // otherwise we'd kill a freshly-running pulse from a follow-on search.
    if (myToken === state.searchToken) {
      $form.classList.remove('is-loading');
    }
  }
}

function describeFilters() {
  const bits = [];
  if (state.roundId) {
    const r = (state.manifest?.rounds || []).find((x) => x.id === state.roundId);
    if (r) bits.push(`in ${r.label}`);
  }
  if (state.committeeSlug) {
    bits.push(`in ${state.committeeSlug.toUpperCase()}`);
  }
  return bits.length ? ' ' + bits.join(', ') : '';
}

// ---------- rendering ----------
// AU Hansard renders portfolio openers in ALL CAPS — "TREASURY PORTFOLIO",
// "FOREIGN AFFAIRS AND TRADE PORTFOLIO". Tonally heavy in a result list;
// title-case for display while preserving the underlying data.
function displaySection(row) {
  const t = row.section || '';
  if (row.section_kind === 'portfolio_header' || /^[A-Z][A-Z0-9 :&'/\-,()]+$/.test(t)) {
    return t.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase());
  }
  return t;
}

function partyOrRoleChips(names, max) {
  if (!names || !names.length) return '';
  const shown = names.slice(0, max);
  const more = names.length - shown.length;
  const pieces = shown.map((n) => `<span class="est-chip">${escapeHtml(n)}</span>`);
  if (more > 0) pieces.push(`<span class="est-chip est-chip-more">+${more}</span>`);
  return pieces.join('');
}

function renderHits(hits) {
  const frag = document.createDocumentFragment();
  for (const row of hits) {
    const li = document.createElement('li');
    li.className = 'est-row';
    const eyebrow = row.committee
      ? escapeHtml(row.committee) + ' · ' + escapeHtml(formatDate(row.date))
      : escapeHtml(formatDate(row.date));
    li.innerHTML = `
      <p class="est-eyebrow">${eyebrow}</p>
      <h2 class="est-section"><a href="${escapeHtml(row.link)}" target="_blank" rel="noopener" title="${escapeHtml(row.section || '')}">${escapeHtml(displaySection(row) || '(untitled)')}</a></h2>
      ${row.questioners?.length ? `<p class="est-roles"><span class="est-roles-label">Q&middot;</span> ${partyOrRoleChips(row.questioners, 5)}</p>` : ''}
      ${row.responders?.length ? `<p class="est-roles"><span class="est-roles-label">A&middot;</span> ${partyOrRoleChips(row.responders, 5)}</p>` : ''}
      <p class="est-snippet">${snippetHtml(row.fullText, state.term, 360)}</p>
    `;
    frag.appendChild(li);
  }
  $results.replaceChildren(frag);
}

function setStatus(text, isError = false) {
  $status.textContent = text;
  $status.classList.toggle('is-error', !!isError);
}

// ---------- filter wiring ----------
async function populateFilters() {
  const m = await getManifest();
  // Rounds — most recent first
  for (const r of (m.rounds || [])) {
    const opt = document.createElement('option');
    opt.value = r.id; opt.textContent = r.label;
    $round.appendChild(opt);
  }
  // Committees — union of slugs across all rounds
  const seen = new Map();
  for (const r of (m.rounds || [])) {
    for (const c of (r.committees || [])) {
      seen.set(c, (seen.get(c) || 0) + 1);
    }
  }
  for (const slug of [...seen.keys()].sort()) {
    const opt = document.createElement('option');
    opt.value = slug; opt.textContent = slug.toUpperCase();
    $committee.appendChild(opt);
  }
}

$form.addEventListener('submit', (e) => {
  e.preventDefault();
  state.term = $q.value.trim();
  state.roundId = $round.value || '';
  state.committeeSlug = $committee.value || '';
  pushUrlState();
  runSearch();
});

[$round, $committee].forEach((el) => {
  el.addEventListener('change', () => {
    state.roundId = $round.value || '';
    state.committeeSlug = $committee.value || '';
    if (state.term || $q.value.trim()) {
      state.term = $q.value.trim();
      pushUrlState();
      runSearch();
    }
  });
});

// ---------- URL state ----------
function pushUrlState() {
  const p = new URLSearchParams();
  if (state.term) p.set('q', state.term);
  if (state.roundId) p.set('round', state.roundId);
  if (state.committeeSlug) p.set('committee', state.committeeSlug);
  const qs = p.toString();
  const url = qs ? `${location.pathname}?${qs}` : location.pathname;
  if (url === location.pathname + location.search) return;
  history.pushState({ houseEstimates: true }, '', url);
}

function applyParamsFromUrl() {
  const p = new URLSearchParams(location.search);
  state.term         = p.get('q') || '';
  state.roundId      = p.get('round') || '';
  state.committeeSlug = p.get('committee') || '';
  $q.value           = state.term;
  $round.value       = state.roundId || '';
  $committee.value   = state.committeeSlug || '';
}

// ---------- last-updated stamp ----------
async function renderStamp() {
  if (!$stamp) return;
  try {
    const m = await getManifest();
    if (m.as_of) {
      $stamp.textContent = `Index updated ${formatDate(m.as_of)}`;
      const ageMs = Date.now() - new Date(m.as_of).getTime();
      if (ageMs > 7 * 24 * 3600 * 1000) $stamp.classList.add('is-stale');
    }
  } catch { /* ignore */ }
}

// ---------- init ----------
(async () => {
  try {
    await populateFilters();
  } catch (e) { console.warn('estimates filter list failed', e); }
  renderStamp();
  applyParamsFromUrl();
  if (state.term || state.roundId || state.committeeSlug) {
    runSearch();
  } else {
    setStatus('Type a term, pick a round, or both.');
  }
})();
