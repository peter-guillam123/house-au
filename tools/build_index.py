#!/usr/bin/env python3
"""
Combine per-day parsed contribution files into the public-facing
shards GitHub Pages will serve.

Output layout at the repo root, mirroring House UK's evidence-*.json
pattern:

    hansard-archives.json    — manifest (rolling + per-quarter list)
    hansard-rolling.json     — last 90 days from today
    hansard-YYYY-Qn.json     — sealed quarterly archives

Each shard JSON is a flat list of contributions (the shape parser
emits). The manifest gives the frontend a one-shot summary so it
knows which shards exist, when they were last built and how many
contributions they hold — same idea as evidence-archives.json in
House UK.

Usage:
    python3 tools/build_index.py
    python3 tools/build_index.py --rolling-days 90 --root .
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path


def quarter_id(d: dt.date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def quarter_bounds(qid: str) -> tuple[dt.date, dt.date]:
    year, q = qid.split("-Q")
    year = int(year)
    q_idx = int(q)
    start_month = (q_idx - 1) * 3 + 1
    start = dt.date(year, start_month, 1)
    if q_idx == 4:
        end = dt.date(year, 12, 31)
    else:
        end = dt.date(year, start_month + 3, 1) - dt.timedelta(days=1)
    return start, end


def load_all_contributions(parsed_root: Path) -> list[dict]:
    contribs: list[dict] = []
    for path in sorted(parsed_root.rglob("*.json")):
        try:
            contribs.extend(json.loads(path.read_text()))
        except json.JSONDecodeError as e:
            print(f"  skip {path}: {e}", file=sys.stderr)
    return contribs


def write_shard(path: Path, items: list[dict]) -> None:
    path.write_text(json.dumps(items, separators=(",", ":")) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", default="tools/parsed-fragments")
    ap.add_argument("--root", default=".",
                    help="Repo root where hansard-*.json shards land")
    ap.add_argument("--rolling-days", type=int, default=90)
    args = ap.parse_args(argv)

    parsed_root = Path(args.parsed)
    out_root = Path(args.root)

    contribs = load_all_contributions(parsed_root)
    if not contribs:
        print(f"No contributions found under {parsed_root}", file=sys.stderr)
        return 1

    contribs.sort(key=lambda c: (c.get("date", ""), c.get("id", "")))

    today = dt.date.today()
    rolling_cutoff = today - dt.timedelta(days=args.rolling_days)

    # Bucket by quarter; the rolling window is *additionally* extracted
    # from whichever quarters overlap it.
    by_quarter: dict[str, list[dict]] = defaultdict(list)
    rolling: list[dict] = []

    for c in contribs:
        try:
            d = dt.date.fromisoformat(c.get("date", ""))
        except ValueError:
            continue
        qid = quarter_id(d)
        # Only the *sealed* quarters go into per-quarter shards — the
        # current quarter rides only inside the rolling window so we
        # don't ship a stale half-quarter archive that pretends to be
        # complete.
        if quarter_id(today) != qid:
            by_quarter[qid].append(c)
        if d >= rolling_cutoff:
            rolling.append(c)

    # Sealed quarterly shards
    quarters_meta = []
    for qid in sorted(by_quarter):
        items = by_quarter[qid]
        path = out_root / f"hansard-{qid}.json"
        write_shard(path, items)
        start, end = quarter_bounds(qid)
        quarters_meta.append({
            "id":          qid,
            "url":         path.name,
            "from":        start.isoformat(),
            "to":          end.isoformat(),
            "count":       len(items),
        })
        print(f"  wrote {path.name}: {len(items)} contributions")

    # Rolling shard (always present — even if empty)
    rolling_path = out_root / "hansard-rolling.json"
    write_shard(rolling_path, rolling)
    print(f"  wrote {rolling_path.name}: {len(rolling)} contributions "
          f"(rolling {args.rolling_days}d)")

    manifest = {
        "as_of":    today.isoformat(),
        "rolling": {
            "url":   rolling_path.name,
            "from":  rolling_cutoff.isoformat(),
            "to":    today.isoformat(),
            "count": len(rolling),
        } if rolling else None,
        "quarters": quarters_meta,
    }
    manifest_path = out_root / "hansard-archives.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {manifest_path.name}: {len(quarters_meta)} quarters + rolling")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
