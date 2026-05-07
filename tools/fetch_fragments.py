#!/usr/bin/env python3
"""
Fetch Hansard fragment JSON for every sitting day in the discovery file.

For each sitting day, two requests:
  1. GET aph.gov.au's Hansard_Display HTML page → parse the TOC's
     data-sid="..." values to enumerate the sections.
  2. For each sid, GET /api/hansard/transcript?id=<bid><sid> → JSON
     payload containing TalkText (HTML), Speaker, Date, Chamber and
     friends.

Why this works: parlinfo's XML download is behind Azure WAF, but
aph.gov.au's own /api/hansard/transcript JSON endpoint is not. Stable
parliamentarian IDs (MPID=...), electorates, ministerial titles and
timestamps all live in TalkText's CSS classes — semantic enough to
parse cleanly downstream.

Each day's fragments are cached to one JSON file:
  tools/raw-fragments/<chamber>/<date>_<id>.json

Resumable: if the file exists, the day is skipped. Bounded concurrency
(4 workers by default) keeps us polite to aph.gov.au and survives
Chrome-iOS-style memory limits if anyone runs this on a phone.

Usage:
    python3 tools/fetch_fragments.py --discovery tools/hansard-ids.json
    python3 tools/fetch_fragments.py --since 2026-04-01 --workers 4
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

DISPLAY_URL = "https://www.aph.gov.au/Parliamentary_Business/Hansard/Hansard_Display"
TRANSCRIPT_URL = "https://www.aph.gov.au/api/hansard/transcript"

CHAMBER_TO_SLUG = {"reps": "hansardr", "senate": "hansards"}

SID_RE = re.compile(r'data-sid="(\d{4})"')


def http_get(url: str, params: dict | None = None, retries: int = 3) -> bytes:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(1.5 ** attempt)
    raise RuntimeError(f"{url}: {last_err}")


def discover_sids(bid_path: str) -> list[str]:
    """Fetch the Hansard_Display page and return all section IDs."""
    html = http_get(DISPLAY_URL, {"bid": bid_path, "sid": "0000"}).decode(
        "utf-8", errors="replace"
    )
    return sorted(set(SID_RE.findall(html)))


def fetch_fragment(bid_path: str, sid: str) -> dict:
    """Fetch one fragment as a JSON dict."""
    body = http_get(TRANSCRIPT_URL, {"id": f"{bid_path}{sid}"})
    return json.loads(body)


def harvest_day(sitting: dict, out_dir: Path, force: bool = False) -> dict:
    """Harvest one sitting day, write a single JSON file with all its fragments."""
    chamber = sitting["chamber"]
    bid_path = f"chamber/{CHAMBER_TO_SLUG[chamber]}/{sitting['id']}/"
    out_path = out_dir / chamber / f"{sitting['date']}_{sitting['id']}.json"
    if out_path.exists() and not force:
        return {"sitting": sitting, "status": "skipped", "fragments": 0}

    out_path.parent.mkdir(parents=True, exist_ok=True)

    sids = discover_sids(bid_path)
    fragments = []
    for sid in sids:
        try:
            fragments.append({"sid": sid, "data": fetch_fragment(bid_path, sid)})
        except Exception as e:
            fragments.append({"sid": sid, "error": str(e)})
        time.sleep(0.1)  # 10 req/s ceiling per worker — polite to aph.gov.au

    payload = {
        "chamber": chamber,
        "date": sitting["date"],
        "bid": bid_path,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "fragments": fragments,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "sitting": sitting,
        "status": "ok",
        "fragments": sum(1 for f in fragments if "data" in f),
        "errors": sum(1 for f in fragments if "error" in f),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discovery", default="tools/hansard-ids.json",
                    help="Path to discovery JSON")
    ap.add_argument("--out", default="tools/raw-fragments",
                    help="Output directory for per-day JSON files")
    ap.add_argument("--since", default=None,
                    help="Only harvest sittings on or after this ISO date")
    ap.add_argument("--workers", type=int, default=4,
                    help="Concurrent sitting-day workers (default 4)")
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch even if the per-day file exists")
    ap.add_argument("--limit", type=int, default=0,
                    help="Cap the number of sittings (for smoke tests)")
    args = ap.parse_args(argv)

    discovery = json.loads(Path(args.discovery).read_text())
    sittings = discovery["sittings"]
    if args.since:
        cutoff = dt.date.fromisoformat(args.since)
        sittings = [s for s in sittings if dt.date.fromisoformat(s["date"]) >= cutoff]
    if args.limit:
        sittings = sittings[: args.limit]

    out_dir = Path(args.out)
    print(f"Harvesting {len(sittings)} sittings → {out_dir} ({args.workers} workers)")

    ok = skipped = failed = total_fragments = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(harvest_day, s, out_dir, args.force): s for s in sittings}
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                failed += 1
                print(f"  FAIL  {s['chamber']} {s['date']} ({s['id']}): {e}")
                continue
            if res["status"] == "skipped":
                skipped += 1
                continue
            ok += 1
            total_fragments += res["fragments"]
            tag = "OK  " if res.get("errors", 0) == 0 else f"OK* "
            print(f"  {tag}{s['chamber']:6} {s['date']} ({s['id']}): "
                  f"{res['fragments']} fragments"
                  + (f", {res['errors']} errors" if res.get("errors") else ""))

    print(f"\nDone. ok={ok} skipped={skipped} failed={failed} fragments={total_fragments}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
