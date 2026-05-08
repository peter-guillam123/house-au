#!/usr/bin/env python3
"""
Discover Senate Estimates committee sessions from the
aph.gov.au transcript schedule page.

The page groups sessions by "round" — Budget Estimates / Supplementary
Estimates / Additional Estimates — using button-toggled <div> wrappers:

    <button id="showHansardTable9" ...>February 2024-2025 Additional Budget Estimates</button>
    ...
    <div id="HansardTableWrapper9">
      <table>
        <tr><td>09/02/2026</td><td><a href=".../Senate_Estimates/fpa">Finance and Public Administration</a></td><td>29359</td><td>...</td></tr>
        ...
      </table>
    </div>

The current/default tab uses HansardTableWrapper (no number); previous
rounds use HansardTableWrapperN. Walk both to enumerate every session.

The page only shows the most recent ~4 rounds (about a year of
material). Older rounds back to 2020 will need a separate archive
harvester — left as a follow-up; the journalistically densest material
lives in the current parliament anyway.

Output shape:
    {
      "as_of": "...",
      "sessions": [
        {
          "round":          "February 2025-2026 Additional Budget Estimates",
          "round_id":       "additional-2025-26",     # slugified
          "date":           "2026-02-09",
          "committee":      "Finance and Public Administration",
          "committee_slug": "fpa",
          "ref":            "29359",
          "bid":            "committees/estimate/29359/",
        },
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import json
import re
import sys
import urllib.request
from pathlib import Path

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

SCHEDULE_URL = (
    "https://www.aph.gov.au/Parliamentary_Business/Hansard/Estimates_Transcript_Schedule"
)

# Maps wrapper id (the trailing number, or '' for the default tab) to the
# round label from the corresponding button.
BUTTON_RE = re.compile(
    r'<button[^>]*id="showHansardTable(\d*)"[^>]*>\s*([^<]+?)\s*</button>',
    re.S,
)
# Wrapper openings only — the body has nested divs that break naive
# `.*?</div>` matching. We slice the HTML between successive opening
# positions instead.
WRAPPER_OPEN_RE = re.compile(r'<div\s+id="HansardTableWrapper(\d*)"', re.S)
# Inside a wrapper: each row is <tr>…<td>date</td>…<td><a …>committee</a></td>…<td>ref</td>…
ROW_RE = re.compile(
    r'<tr>\s*'
    r'<td[^>]*>\s*&nbsp;?(\d{2}/\d{2}/\d{4})\s*(?:<br\s*/?>)?\s*</td>\s*'
    r'<td[^>]*>\s*&nbsp;?<a[^>]*href="([^"]*)"[^>]*>\s*([^<]+?)\s*</a>\s*(?:<br\s*/?>)?\s*</td>\s*'
    r'<td[^>]*>\s*&nbsp;?(\d+)\s*(?:<br\s*/?>)?\s*</td>',
    re.S,
)

COMMITTEE_SLUG_RE = re.compile(r"/Senate_Estimates/([a-z0-9_-]+)", re.I)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def slug_round(label: str) -> str:
    """'February 2025-2026 Additional Budget Estimates' → 'additional-2025-26'."""
    L = label.lower()
    if "supplementary" in L:
        kind = "supplementary"
    elif "additional" in L:
        kind = "additional"
    else:
        kind = "budget"
    yrs = re.search(r"(\d{4})\s*-\s*(\d{2,4})", L)
    if yrs:
        a, b = yrs.group(1), yrs.group(2)
        b = b[-2:]
        return f"{kind}-{a}-{b}"
    return kind


def parse_date(au_date: str) -> str:
    """'09/02/2026' → '2026-02-09'."""
    try:
        return dt.datetime.strptime(au_date, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return ""


def _synthesise_round_label(dates: list[str]) -> str:
    """Best-effort label for a wrapper that has no button (the default tab).

    AU's three Estimates rounds align with predictable months:
        Feb-Mar  → Additional Budget Estimates
        May-Jun  → Budget Estimates
        Oct-Nov  → Supplementary Budget Estimates
    Use the dominant month + year to label."""
    if not dates:
        return "Estimates"
    iso = max(dates)  # most recent date
    y, m, _ = iso.split("-")
    month = int(m)
    if 2 <= month <= 4:
        kind = "Additional Budget Estimates"
    elif 5 <= month <= 7:
        kind = "Budget Estimates"
    elif 9 <= month <= 12:
        kind = "Supplementary Budget Estimates"
    else:
        kind = "Estimates"
    return f"{y} {kind}"


def parse_schedule(html: str) -> list[dict]:
    button_label = {bid: label.strip() for bid, label in BUTTON_RE.findall(html)}

    # Slice the doc between wrapper openings — the bodies contain nested
    # divs that defeat naive `.*?</div>` matching, so we walk by start
    # positions instead and treat each window as that wrapper's body.
    opens = [(m.start(), m.group(1)) for m in WRAPPER_OPEN_RE.finditer(html)]
    if not opens:
        return []
    bounds = opens + [(len(html), "")]

    sessions: list[dict] = []
    for i, (start, wid) in enumerate(opens):
        end = bounds[i + 1][0]
        body = html[start:end]

        rows = ROW_RE.findall(body)
        if not rows:
            continue

        round_label = html_lib.unescape(button_label.get(wid, ""))
        if not round_label:
            iso_dates = [parse_date(d) for d, *_ in rows]
            iso_dates = [d for d in iso_dates if d]
            round_label = _synthesise_round_label(iso_dates)
        round_id = slug_round(round_label)

        for date_au, comm_url, comm_name, ref in rows:
            slug_m = COMMITTEE_SLUG_RE.search(comm_url)
            sessions.append({
                "round":          round_label,
                "round_id":       round_id,
                "date":           parse_date(date_au),
                "committee":      comm_name.strip(),
                "committee_slug": slug_m.group(1).lower() if slug_m else "",
                "ref":            ref,
                "bid":            f"committees/estimate/{ref}/",
            })
    return sessions


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="tools/estimates-ids.json")
    args = ap.parse_args(argv)

    html = fetch(SCHEDULE_URL)
    sessions = parse_schedule(html)
    sessions.sort(key=lambda s: (s["date"], s["committee_slug"], s["ref"]))

    payload = {
        "as_of":    dt.date.today().isoformat(),
        "sessions": sessions,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    rounds: dict[str, int] = {}
    for s in sessions:
        rounds[s["round"]] = rounds.get(s["round"], 0) + 1
    print(f"Wrote {len(sessions)} Estimates sessions to {out}")
    for r in sorted(rounds):
        print(f"  {rounds[r]:3}  {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
