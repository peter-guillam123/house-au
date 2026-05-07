#!/usr/bin/env python3
"""
Discover federal Australian chamber Hansard sitting days.

Both listing pages on aph.gov.au — one for House of Reps, one for the
Senate — render every available sitting day as a single static HTML
table, year by year. Each day's link carries:

    href="...?bid=chamber/hansardr/29339/&sid=0000"
    aria-label="19-Jan-2026"

So we just curl the two pages and pull out (chamber, id, date) tuples
with a regex. No Playwright, no auth, no JS challenge — these pages
aren't behind the WAF that protects parlinfo's XML downloads.

Usage:
    python3 tools/discover_hansards.py --since 2020-01-01 --out tools/hansard-ids.json

Output JSON shape:
    {
      "as_of": "2026-05-07",
      "since": "2020-01-01",
      "sittings": [
        {"chamber": "reps",   "id": "29339", "date": "2026-01-19"},
        {"chamber": "senate", "id": "29341", "date": "2026-01-19"},
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

REPS_LISTING   = "https://www.aph.gov.au/Parliamentary_Business/Hansard/Hansreps_2011"
SENATE_LISTING = "https://www.aph.gov.au/Parliamentary_Business/Hansard/Hanssen261110"

# Real-browser UA — aph.gov.au sometimes serves a stripped page to non-browser
# clients, and we'd rather not chase that ambiguity.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# bid=chamber/hansardr/29339/                                  (modern numeric)
# bid=chamber/hansardr/0fd15237-92f1-4dbd-bab5-e7bba7ee781d/    (older UUID)
# Both formats coexist — the listing page uses UUIDs for ~2020 and earlier,
# and short numeric IDs for ~late 2021 onwards. The XML download path
# accepts either, but they're routed differently downstream.
ENTRY_RE = re.compile(
    r'href="[^"]*bid=chamber/(hansardr|hansards)/([^/]+)/[^"]*"'
    r'[^>]*aria-label="(\d{1,2}-[A-Za-z]{3}-\d{4})"'
)

CHAMBER = {"hansardr": "reps", "hansards": "senate"}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_listing(html: str) -> list[dict]:
    out = []
    for m in ENTRY_RE.finditer(html):
        slug, hid, label = m.group(1), m.group(2), m.group(3)
        try:
            d = dt.datetime.strptime(label, "%d-%b-%Y").date()
        except ValueError:
            continue
        out.append({"chamber": CHAMBER[slug], "id": hid, "date": d.isoformat()})
    return out


def discover(since: dt.date) -> list[dict]:
    sittings: list[dict] = []
    for url in (REPS_LISTING, SENATE_LISTING):
        sittings.extend(parse_listing(fetch(url)))
    seen: set[tuple[str, str]] = set()
    deduped = []
    for s in sittings:
        key = (s["chamber"], s["id"])
        if key in seen:
            continue
        seen.add(key)
        if dt.date.fromisoformat(s["date"]) >= since:
            deduped.append(s)
    deduped.sort(key=lambda s: (s["date"], s["chamber"], s["id"]))
    return deduped


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", default="2020-01-01",
                    help="ISO date; sittings before this are filtered out")
    ap.add_argument("--out", default="tools/hansard-ids.json",
                    help="Output JSON path")
    args = ap.parse_args(argv)

    since = dt.date.fromisoformat(args.since)
    sittings = discover(since)

    payload = {
        "as_of": dt.date.today().isoformat(),
        "since": since.isoformat(),
        "sittings": sittings,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    by_chamber: dict[str, int] = {}
    for s in sittings:
        by_chamber[s["chamber"]] = by_chamber.get(s["chamber"], 0) + 1
    print(f"Wrote {len(sittings)} sitting days to {out}")
    for k, v in sorted(by_chamber.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
