// House AU — data layer over the static shards built by the harvester.
//
// The frontend was written for House UK's live-API model. Here it
// reads pre-built JSON shards from GitHub Pages: hansard-archives.json
// (manifest) plus one shard per quarter and a rolling 90-day file.
// Each search runs locally against the shards that overlap the
// requested date range — fetched lazily, cached in memory by URL,
// evicted when memory pressure suggests we should (~Chrome iOS).
//
// Function signatures and return shapes mirror UK's api.js so the rest
// of the frontend doesn't care that the source changed.

import { unquoteTerm, buildSearchRegex } from './format.js?v=4';

export const PROXY = '';  // unused — kept for parity with House UK

const MANIFEST_URL = './hansard-archives.json';

// ---------------- Manifest + shard loading ----------------

let _manifestPromise = null;
function getManifest() {
  if (!_manifestPromise) {
    _manifestPromise = fetch(MANIFEST_URL).then(async (r) => {
      if (!r.ok) throw new Error(`manifest ${r.status}`);
      return r.json();
    }).catch((e) => {
      _manifestPromise = null;  // allow retry on later call
      throw e;
    });
  }
  return _manifestPromise;
}

const _shardCache = new Map();           // url -> Promise<Array>
const _shardOrder = [];                  // LRU order of cached urls
const SHARD_CACHE_MAX = 6;               // keep memory bounded on mobile

function _touchShard(url) {
  const i = _shardOrder.indexOf(url);
  if (i >= 0) _shardOrder.splice(i, 1);
  _shardOrder.push(url);
  while (_shardOrder.length > SHARD_CACHE_MAX) {
    const drop = _shardOrder.shift();
    _shardCache.delete(drop);
  }
}

function loadShard(url) {
  if (!_shardCache.has(url)) {
    _shardCache.set(url, fetch(`./${url}`).then(async (r) => {
      if (!r.ok) throw new Error(`shard ${url} ${r.status}`);
      return r.json();
    }));
  }
  _touchShard(url);
  return _shardCache.get(url);
}

// Pick the shards whose date range overlaps [startDate, endDate].
// Always include rolling if no explicit date range is given (so a
// no-filter search shows the most recent material first).
async function shardsForRange(startDate, endDate) {
  const m = await getManifest();
  const out = [];
  if (m.rolling) out.push(m.rolling.url);
  for (const q of (m.quarters || [])) {
    if (startDate && q.to < startDate) continue;
    if (endDate && q.from > endDate) continue;
    out.push(q.url);
  }
  return out;
}

// Eagerly load all shards we need, dedupe by id (rolling + quarter
// can overlap on the seam), return one flat array.
async function gatherContributions(opts) {
  const urls = await shardsForRange(opts.startDate, opts.endDate);
  const all = await Promise.all(urls.map(loadShard));
  const seen = new Set();
  const out = [];
  for (const arr of all) {
    for (const c of arr) {
      if (seen.has(c.id)) continue;
      seen.add(c.id);
      out.push(c);
    }
  }
  return out;
}

// ---------------- Filtering ----------------

const HOUSE_TO_CHAMBER = { 'Reps': 'reps', 'Senate': 'senate' };

function passesFilters(c, opts) {
  if (opts.startDate && c.date < opts.startDate) return false;
  if (opts.endDate && c.date > opts.endDate) return false;
  if (opts.house && opts.house !== 'Both') {
    const want = HOUSE_TO_CHAMBER[opts.house];
    if (want && c.chamber !== want) return false;
  }
  if (opts.memberId != null && String(c.speakerId) !== String(opts.memberId)) return false;
  return true;
}

function termMatcher(term) {
  if (!term) return () => true;
  const re = buildSearchRegex(term);
  return (c) => re.test(c.fullText || '');
}

// Internal parser values are compact ("QuestionWithoutNotice"); the UI
// reads better with short labels.
const SOURCE_DISPLAY = {
  Spoken:                'Spoken',
  QuestionWithoutNotice: 'Q without notice',
  QuestionOnNotice:      'Q on notice',
  Statement:             'Statement',
};

// AU debate titles are routinely shaped like:
//   "COMMITTEES - Impact of the Conflict in Iran Select Committee - Appointment"
//   "BUSINESS - Days and Hours of Meeting"
//   "BILLS - National Reconstruction Fund Bill 2023 - Second Reading"
// The leading ALL-CAPS segment is parliament's section tag. Treating it
// as the title proper makes the row visually shouty and duplicates the
// `context` field we already have. Split it off into an eyebrow.
//
// A second pattern: some fragments have *just* the section header as
// the entire title ("COMMITTEES"). No dash, no detail — it's a top-of-
// section opener whose substance lives in the speech body. Title-case
// it for display so we don't render a wall of caps as a heading.
const ALL_CAPS_HEADER = /^[A-Z][A-Z0-9 &/'-]{1,60}$/;

function titleCaseAcronymSafe(s) {
  return s.toLowerCase().replace(/\b[a-z]/g, c => c.toUpperCase());
}

function splitTitle(rawTitle) {
  if (!rawTitle) return { eyebrow: '', title: '' };
  const trimmed = rawTitle.trim();
  const m = trimmed.match(/^([A-Z][A-Z0-9 &/'-]{1,40})\s*-\s*(.+)$/);
  if (m) return { eyebrow: m[1].trim(), title: m[2].trim() };
  if (ALL_CAPS_HEADER.test(trimmed)) {
    return { eyebrow: '', title: titleCaseAcronymSafe(trimmed) };
  }
  return { eyebrow: '', title: trimmed };
}

function toContribution(c) {
  const house = c.chamber === 'reps' ? 'Reps'
              : c.chamber === 'senate' ? 'Senate' : '';
  const memberName = c.electorate
    ? `${c.speakerName} (${c.electorate})`
    : c.speakerName;
  const { eyebrow, title } = splitTitle(c.title || '');
  return {
    eyebrow,
    source:       SOURCE_DISPLAY[c.source] || c.source,
    id:           c.id,
    date:         c.date,
    house,
    memberId:     c.speakerId || null,
    memberName,
    shortName:    c.shortName || c.speakerName || '',
    party:        '',                      // not yet enriched
    title:        title || c.title || '',
    section:      c.context || eyebrow || '',
    debateExtId:  '',                      // AU links are direct, no ext-id needed
    snippet:      c.fullText || '',
    fullText:     c.fullText || '',
    link:         c.link || '',
  };
}

async function runSearch(sourceMatch, opts) {
  const all = await gatherContributions(opts);
  const matchTerm = termMatcher(opts.searchTerm);
  const hits = [];
  for (const c of all) {
    if (!sourceMatch(c.source)) continue;
    if (!passesFilters(c, opts)) continue;
    if (!matchTerm(c)) continue;
    hits.push(c);
  }
  hits.sort((a, b) => (b.date || '').localeCompare(a.date || '') || b.id.localeCompare(a.id));
  const skip = opts.skip ?? 0;
  const take = opts.take ?? 20;
  return {
    total: hits.length,
    items: hits.slice(skip, skip + take).map(toContribution),
  };
}

// ---------------- Public search functions ----------------

const SRC = {
  spoken:        (s) => s === 'Spoken' || s === 'QuestionWithoutNotice',
  written:       (s) => s === 'QuestionOnNotice' || s === 'Statement',
  wq:            (s) => s === 'QuestionOnNotice',
  ws:            (s) => s === 'Statement',
  committee:     (_s) => false,           // chamber-only harvest for now
  any:           () => true,
};

export async function searchSpoken(opts)            { return runSearch(SRC.spoken,    opts); }
export async function searchWrittenHansard(opts)    { return runSearch(SRC.written,   opts); }
export async function searchCommitteeDebates(opts)  { return runSearch(SRC.committee, opts); }
export async function searchWrittenQuestions(opts)  { return runSearch(SRC.wq,        opts); }
export async function searchWrittenStatements(opts) { return runSearch(SRC.ws,        opts); }

// ---------------- Timeline stats (Deep Dive) ----------------

export async function timelineStats(opts) {
  const all = await gatherContributions(opts);
  const matchTerm = termMatcher(opts.searchTerm);
  const want = opts.contributionType === 'Written' ? SRC.written : SRC.spoken;
  const buckets = new Map();
  let total = 0;
  for (const c of all) {
    if (!want(c.source)) continue;
    if (!passesFilters(c, opts)) continue;
    if (!matchTerm(c)) continue;
    const month = (c.date || '').slice(0, 7);
    if (!month) continue;
    buckets.set(month, (buckets.get(month) || 0) + 1);
    total += 1;
  }
  const out = [...buckets.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, count]) => ({ month, count }));
  return { total, buckets: out };
}

// ---------------- Members ----------------
//
// AU has no party data on the contribution rows yet (parser TODO). For
// now, members are derived by scanning loaded shards for distinct
// speakerIds. memberById returns the most recent appearance.

let _memberIndexPromise = null;
async function getMemberIndex() {
  if (_memberIndexPromise) return _memberIndexPromise;
  _memberIndexPromise = (async () => {
    const all = await gatherContributions({});  // load whatever's in cache; manifest covers all quarters
    const byId = new Map();
    for (const c of all) {
      const id = c.speakerId || '';
      if (!id) continue;
      const existing = byId.get(id);
      if (!existing || (c.date > existing.lastSeen)) {
        byId.set(id, {
          id,
          name:        c.speakerName || c.shortName || '',
          shortName:   c.shortName || '',
          electorate:  c.electorate || '',
          chamber:     c.chamber,
          lastSeen:    c.date || '',
        });
      }
    }
    return byId;
  })();
  return _memberIndexPromise;
}

export async function memberById(id) {
  if (id == null) return null;
  const idx = await getMemberIndex();
  return idx.get(String(id)) || null;
}

export async function membersByName(name) {
  if (!name) return [];
  const idx = await getMemberIndex();
  const needle = String(name).toLowerCase();
  return [...idx.values()].filter((m) =>
    (m.name || '').toLowerCase().includes(needle) ||
    (m.shortName || '').toLowerCase().includes(needle),
  );
}

// Party-based filtering doesn't apply yet — AU contributions don't
// carry party. Returning an empty list keeps the filter UI dormant
// rather than crashing.
export async function membersByPartyId(_partyId) { return []; }
export async function listCurrentParties() { return []; }

// ---------------- Committees / inquiries / oral evidence ----------------
//
// Not yet harvested. Surfaces stay rendered in their empty state.

const EMPTY_PAGE = Object.freeze({ total: 0, items: [] });

export async function searchInquiries(_opts)        { return EMPTY_PAGE; }
export async function searchOralEvidence(_opts)     { return EMPTY_PAGE; }
export async function inquiryById(_id)              { return null; }
export async function oralEvidenceTranscript(_id)   { return null; }
