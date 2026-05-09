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
const MEMBERS_URL  = './members.json';

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

// ---------------- Members directory ----------------
//
// members.json is a static MPID → { name, party, chamber } map produced
// by tools/build_members.py. Loaded once, cached, then read synchronously
// from toContribution() and the filter helpers. If the file's missing
// (e.g., a pre-enrichment deploy), fall back to an empty map — the
// pages still work, just without party chips and an empty party filter.

let _members = null;        // synchronous-readable once warmed
let _membersPromise = null;

async function ensureMembers() {
  if (_members) return _members;
  if (!_membersPromise) {
    _membersPromise = fetch(MEMBERS_URL).then(async (r) => {
      if (!r.ok) return { members: {} };
      return r.json();
    }).catch(() => ({ members: {} }));
  }
  const d = await _membersPromise;
  _members = d.members || {};
  return _members;
}

function memberInfo(mpid) {
  if (!mpid || !_members) return null;
  return _members[mpid] || null;
}

// Public: a one-shot getter for the manifest's as_of date so the
// frontend can render a "last updated" line. Returns ISO date or ''.
export async function getIndexDate() {
  try {
    const m = await getManifest();
    return m.as_of || '';
  } catch {
    return '';
  }
}

const _shardCache = new Map();           // url -> Promise<Array>
const _shardOrder = [];                  // LRU order of cached urls
// Conservative cap. Each shard parses to ~25-300MB of JS objects depending
// on how dense the period was; with 6 in cache simultaneously we'd routinely
// blow Chrome's per-tab budget on wide searches. Three is enough to keep
// recent navigation cheap without holding the world.
const SHARD_CACHE_MAX = 3;

function _touchShard(url) {
  const i = _shardOrder.indexOf(url);
  if (i >= 0) _shardOrder.splice(i, 1);
  _shardOrder.push(url);
  while (_shardOrder.length > SHARD_CACHE_MAX) {
    const drop = _shardOrder.shift();
    _shardCache.delete(drop);
  }
}

// Each shard spec is { url, from, to, count, compressed? }. Live shards
// are per-month raw JSON; archive shards are per-quarter gzipped JSON
// (decompressed via DecompressionStream — supported in every browser
// that matters for an internal newsroom tool).
async function _fetchShard(spec) {
  const r = await fetch(`./${spec.url}`);
  if (!r.ok) throw new Error(`shard ${spec.url} ${r.status}`);
  if (spec.compressed) {
    const ds = new DecompressionStream('gzip');
    const text = await new Response(r.body.pipeThrough(ds)).text();
    return JSON.parse(text);
  }
  return r.json();
}

function loadShard(spec) {
  if (!_shardCache.has(spec.url)) {
    _shardCache.set(spec.url, _fetchShard(spec));
  }
  _touchShard(spec.url);
  return _shardCache.get(spec.url);
}

// Pick the shards whose date range overlaps [startDate, endDate].
// Always include rolling if a manifest rolling entry exists.
async function shardsForRange(startDate, endDate) {
  const m = await getManifest();
  const out = [];
  if (m.rolling) out.push(m.rolling);
  // The build writes a unified `shards` array; older builds wrote
  // `quarters`. Read whichever's there.
  const list = m.shards || m.quarters || [];
  for (const s of list) {
    if (startDate && s.to < startDate) continue;
    if (endDate && s.from > endDate) continue;
    out.push(s);
  }
  return out;
}

// Stream shards in small parallel batches, calling visit(contribution) on
// each row that hasn't already been seen (rolling + per-month overlap on
// the seam). The visitor decides whether to keep, count or discard — the
// caller is responsible for memory of anything it accumulates. Batches of
// SHARD_BATCH overlap network round-trips while keeping peak parsed
// memory bounded; sequential iteration was correct but ~3x slower than
// it needed to be on wide searches.
// Bumped from 4 to 6 for ~50% more network parallelism on wide
// "last 5 years" searches. The LRU cap of 3 keeps long-term cache
// pressure unchanged; in-flight memory peaks at 6 parsed shards
// during the batch boundary, well within Chrome's per-tab budget
// after the headlines/snippet cleanup.
const SHARD_BATCH = 6;
async function streamContributions(opts, visit) {
  const specs = await shardsForRange(opts.startDate, opts.endDate);
  const seen = new Set();
  for (let i = 0; i < specs.length; i += SHARD_BATCH) {
    const batch = specs.slice(i, i + SHARD_BATCH);
    const shards = await Promise.all(batch.map(loadShard));
    for (const arr of shards) {
      for (const c of arr) {
        if (seen.has(c.id)) continue;
        seen.add(c.id);
        visit(c);
      }
    }
  }
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
  // Party id is the long-form party name for AU (no numeric ids on
  // aph.gov.au's parliamentarian profiles). The dropdown's option value
  // is the same name string, so an exact-match check works.
  if (opts.partyId != null && opts.partyId !== '') {
    const m = memberInfo(c.speakerId);
    if (!m || m.party !== opts.partyId) return false;
  }
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
//   "QUESTIONS WITHOUT NOTICE: TAKE NOTE OF ANSWERS - Budget: Fuel"
// The leading ALL-CAPS segment is parliament's section tag. Treating it
// as the title proper makes the row visually shouty and duplicates the
// `context` field we already have. Split it off into an eyebrow.
//
// A second pattern: some fragments have *just* the section header as
// the entire title ("COMMITTEES", "QUESTIONS WITHOUT NOTICE: TAKE NOTE
// OF ANSWERS"). No dash, no detail — it's a top-of-section opener whose
// substance lives in the speech body. Title-case it for display so we
// don't render a wall of caps as a heading.
//
// The character class allows colons because some compound prefixes use
// them ("QUESTIONS WITHOUT NOTICE: TAKE NOTE OF ANSWERS"); length cap
// is 80 because real prefixes routinely run 40-50 chars.
const ALL_CAPS_HEADER = /^[A-Z][A-Z0-9 :&'/-]{1,80}$/;

function titleCaseAcronymSafe(s) {
  return s.toLowerCase().replace(/\b[a-z]/g, c => c.toUpperCase());
}

function splitTitle(rawTitle) {
  if (!rawTitle) return { eyebrow: '', title: '' };
  const trimmed = rawTitle.trim();
  const m = trimmed.match(/^([A-Z][A-Z0-9 :&'/-]{1,80})\s*-\s*(.+)$/);
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
  const rawTitle = c.title || '';
  const { eyebrow, title } = splitTitle(rawTitle);
  const m = memberInfo(c.speakerId);
  // Synthesise a debate key from (date, raw title) so Deep Dive's
  // "In these debates" panel can group AU contributions. UK had a
  // canonical debateExtId from the Hansard API; AU's site has no
  // equivalent, so we hash on the natural editorial unit (one debate
  // = one (sitting day, debate title) pair). Two days with an
  // "Adjournment" debate stay correctly distinct.
  const debateExtId = rawTitle ? `${c.date || ''}|${rawTitle}` : '';
  return {
    eyebrow,
    source:       SOURCE_DISPLAY[c.source] || c.source,
    id:           c.id,
    date:         c.date,
    house,
    memberId:     c.speakerId || null,
    memberName,
    shortName:    c.shortName || c.speakerName || '',
    party:        (m && m.party) || '',
    title:        title || c.title || '',
    section:      c.context || eyebrow || '',
    debateExtId,
    snippet:      c.fullText || '',
    fullText:     c.fullText || '',
    link:         c.link || '',
  };
}

async function runSearch(sourceMatch, opts) {
  await ensureMembers();           // warm party lookups for filter + display
  const matchTerm = termMatcher(opts.searchTerm);
  const hits = [];
  await streamContributions(opts, (c) => {
    if (!sourceMatch(c.source)) return;
    if (!passesFilters(c, opts)) return;
    if (!matchTerm(c)) return;
    hits.push(c);
  });
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
  any:           () => true,
};

export async function searchSpoken(opts)            { return runSearch(SRC.spoken,    opts); }
export async function searchWrittenHansard(opts)    { return runSearch(SRC.written,   opts); }
export async function searchWrittenQuestions(opts)  { return runSearch(SRC.wq,        opts); }
export async function searchWrittenStatements(opts) { return runSearch(SRC.ws,        opts); }

// ---------------- Timeline stats (Deep Dive) ----------------

export async function timelineStats(opts) {
  await ensureMembers();
  const matchTerm = termMatcher(opts.searchTerm);
  const want = opts.contributionType === 'Written' ? SRC.written : SRC.spoken;
  const buckets = new Map();
  let total = 0;
  await streamContributions(opts, (c) => {
    if (!want(c.source)) return;
    if (!passesFilters(c, opts)) return;
    if (!matchTerm(c)) return;
    const month = (c.date || '').slice(0, 7);
    if (!month) return;
    buckets.set(month, (buckets.get(month) || 0) + 1);
    total += 1;
  });
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
    const byId = new Map();
    await streamContributions({}, (c) => {
      const id = c.speakerId || '';
      if (!id) return;
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
    });
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

// Party data comes from members.json. We use the long-form party name
// as the id (aph.gov.au profiles don't expose numeric ids).
export async function listCurrentParties() {
  const m = await ensureMembers();
  const counts = new Map();
  for (const v of Object.values(m)) {
    if (!v.party) continue;
    counts.set(v.party, (counts.get(v.party) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name]) => ({ id: name, name }));
}

export async function membersByPartyId(partyId) {
  if (!partyId) return [];
  const m = await ensureMembers();
  const out = [];
  for (const [mpid, info] of Object.entries(m)) {
    if (info.party === partyId) out.push(mpid);
  }
  return out;
}

