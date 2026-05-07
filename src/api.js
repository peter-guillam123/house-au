// House AU — scaffold-mode data layer.
//
// The harvester (tools/build-house-au-index.py, not yet written) will
// commit a set of static JSON files (hansard-YYYY-Q*.json + a rolling
// 90-day window) to the repo. Once that exists, this module is rewritten
// to load and search those files client-side. For now it exports the
// same surface as House UK's api.js but every function returns empty
// data, so each page renders its natural "no results" state instead of
// crashing on a missing UK Parliament endpoint.

const SCAFFOLD_NOTICE_KEY = '__house_au_scaffold_warned';
if (typeof window !== 'undefined' && !window[SCAFFOLD_NOTICE_KEY]) {
  console.info(
    'House AU is in scaffold mode. The data layer is stubbed — searches ' +
    'return empty results until the harvester is wired up.',
  );
  window[SCAFFOLD_NOTICE_KEY] = true;
}

// PROXY is exported for parity with House UK; AU has no Worker.
export const PROXY = '';

const EMPTY_PAGE = Object.freeze({ total: 0, items: [] });

export async function timelineStats(_opts) {
  return { total: 0, buckets: [] };
}

export async function searchSpoken(_opts) { return EMPTY_PAGE; }
export async function searchWrittenHansard(_opts) { return EMPTY_PAGE; }
export async function searchCommitteeDebates(_opts) { return EMPTY_PAGE; }
export async function searchWrittenQuestions(_opts) { return EMPTY_PAGE; }
export async function searchWrittenStatements(_opts) { return EMPTY_PAGE; }

export async function memberById(_id) { return null; }
export async function membersByName(_name) { return []; }
export async function membersByPartyId(_partyId) { return []; }
export async function listCurrentParties() { return []; }

export async function searchInquiries(_opts) { return EMPTY_PAGE; }
export async function searchOralEvidence(_opts) { return EMPTY_PAGE; }
export async function inquiryById(_id) { return null; }
export async function oralEvidenceTranscript(_id) { return null; }
