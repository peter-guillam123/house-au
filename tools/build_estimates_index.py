#!/usr/bin/env python3
"""
Build per-round Estimates shards from parsed section rows.

Produces:

    estimates-{round_id}.json      one file per Estimates round
    estimates-archives.json        manifest pointing at each round

Estimates rounds run three times a year (Budget / Supplementary /
Additional). The visible page on aph.gov.au shows ~4 rounds (about a
year of material), each yielding 4-30 sessions, each session yielding
6-15 section rows. Total volume per round lands at a few megabytes —
small enough to ship raw without per-month splitting or gzip; if a
round ever balloons we revisit.

Manifest shape mirrors hansard-archives.json but with round-level
metadata: id, label, date range, session count, section count,
distinct committees.

Usage:
    python3 tools/build_estimates_index.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path


def load_all_rows(parsed_root: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(parsed_root.glob("*.json")):
        try:
            rows.extend(json.loads(path.read_text()))
        except json.JSONDecodeError as e:
            print(f"  skip {path}: {e}", file=sys.stderr)
    return rows


def write_raw(path: Path, items: list) -> None:
    path.write_text(json.dumps(items, separators=(",", ":")) + "\n", encoding="utf-8")


def clear_existing_estimates_shards(root: Path) -> None:
    """Wipe estimates-*.json before rebuilding so old rounds don't
    survive a relabel. Doesn't touch hansard-* files."""
    for p in root.glob("estimates-*.json"):
        if p.name == "estimates-archives.json":
            continue
        p.unlink()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", default="tools/parsed-estimates")
    ap.add_argument("--root", default=".",
                    help="Repo root where estimates-*.json shards land")
    ap.add_argument("--min-parsed-sessions", type=int, default=10,
                    help="Refuse to rebuild if fewer than this many parsed "
                         "session files exist. Belt-and-braces against a CI "
                         "cache miss wiping the published shards.")
    args = ap.parse_args(argv)

    parsed_root = Path(args.parsed)
    out_root = Path(args.root)

    # Safety check — same pattern as build_index.py: don't let a half-empty
    # cache wipe the published Estimates shards.
    n_parsed = sum(1 for _ in parsed_root.glob("*.json")) if parsed_root.exists() else 0
    if args.min_parsed_sessions and n_parsed < args.min_parsed_sessions:
        print(f"REFUSING to rebuild: only {n_parsed} parsed sessions under {parsed_root} "
              f"(< {args.min_parsed_sessions}). The cache is likely empty. Run "
              f"discover/fetch/parse first, or pass --min-parsed-sessions=0 if "
              f"you really mean a sparse build.", file=sys.stderr)
        return 2

    rows = load_all_rows(parsed_root)
    if not rows:
        print(f"No section rows under {parsed_root}", file=sys.stderr)
        return 1

    # Group by round_id; sort within each by date + committee + sid for
    # stable shard contents.
    by_round: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        rid = r.get("round_id") or "unknown"
        by_round[rid].append(r)
    for rid in by_round:
        by_round[rid].sort(key=lambda r: (r.get("date", ""), r.get("committee_slug", ""), r.get("sid", "")))

    clear_existing_estimates_shards(out_root)

    rounds_meta: list[dict] = []
    for rid in sorted(by_round):
        items = by_round[rid]
        path = out_root / f"estimates-{rid}.json"
        write_raw(path, items)

        dates = [r["date"] for r in items if r.get("date")]
        sessions = {r.get("ref") for r in items if r.get("ref")}
        committees = {r.get("committee_slug") for r in items if r.get("committee_slug")}
        # Round label varies slightly across rows (different sessions can
        # carry slightly different button text); pick the most common.
        labels: dict[str, int] = {}
        for r in items:
            lbl = r.get("round") or ""
            if lbl:
                labels[lbl] = labels.get(lbl, 0) + 1
        label = max(labels, key=labels.get) if labels else rid

        rounds_meta.append({
            "id":            rid,
            "url":           path.name,
            "label":         label,
            "from":          min(dates) if dates else "",
            "to":            max(dates) if dates else "",
            "sessions":      len(sessions),
            "committees":    sorted(committees),
            "section_count": len(items),
        })
        print(f"  wrote {path.name}: {len(items)} sections, "
              f"{len(sessions)} sessions, {len(committees)} committees")

    manifest = {
        "as_of":  dt.date.today().isoformat(),
        "rounds": sorted(rounds_meta, key=lambda m: m["from"], reverse=True),
    }
    manifest_path = out_root / "estimates-archives.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\n  wrote {manifest_path.name}: {len(rounds_meta)} rounds")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
