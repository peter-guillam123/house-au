#!/usr/bin/env python3
"""
Build the public-facing shards from parsed contributions.

Three tiers, all served from the repo root:

    hansard-rolling.json        rolling 90-day window (raw)
    hansard-YYYY-MM.json        last ~24 months, per-month (raw)
    hansard-YYYY-Qn.json.gz     older sealed quarters (gzipped)
    hansard-archives.json       manifest pointing at all of the above

Why three tiers? AU's contribution density (~270 per sitting day) makes
per-quarter raw shards swell to 90MB+, which trips GitHub's per-file
warnings and bloats repo pack size. Per-month splitting keeps each live
file under ~30MB; gzipping the older sealed material gets each
quarterly archive down to 5-8MB. The browser detects the .gz extension
via the manifest's `compressed: true` flag and decompresses on read.

Live cutoff is the quarter containing today minus 730 days — quarters
fully before that boundary go into the gzipped archive; quarters at or
after split per-month. The cutoff slides forward over time, so the
oldest live month migrates into the archive every now and then; that's
fine as long as the build is rerun.

Usage:
    python3 tools/build_index.py
    python3 tools/build_index.py --rolling-days 90 --live-days 730
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path


def quarter_id(d: dt.date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def quarter_bounds(qid: str) -> tuple[dt.date, dt.date]:
    year, q = qid.split("-Q")
    year, q_idx = int(year), int(q)
    start_month = (q_idx - 1) * 3 + 1
    start = dt.date(year, start_month, 1)
    if q_idx == 4:
        end = dt.date(year, 12, 31)
    else:
        end = dt.date(year, start_month + 3, 1) - dt.timedelta(days=1)
    return start, end


def month_bounds(month_key: str) -> tuple[dt.date, dt.date]:
    year, month = (int(x) for x in month_key.split("-"))
    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year, 12, 31)
    else:
        end = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    return start, end


def load_all_contributions(parsed_root: Path) -> list[dict]:
    contribs: list[dict] = []
    for path in sorted(parsed_root.rglob("*.json")):
        try:
            contribs.extend(json.loads(path.read_text()))
        except json.JSONDecodeError as e:
            print(f"  skip {path}: {e}", file=sys.stderr)
    return contribs


def write_raw(path: Path, items: list[dict]) -> None:
    path.write_text(
        json.dumps(items, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def write_gzipped(path: Path, items: list[dict]) -> None:
    body = json.dumps(items, separators=(",", ":")).encode("utf-8")
    # mtime=0 makes the output deterministic — same content → same bytes →
    # same git blob, so re-running the build doesn't churn the pack.
    with gzip.GzipFile(filename=str(path), mode="wb", mtime=0, compresslevel=9) as f:
        f.write(body)


def clear_existing_shards(root: Path) -> None:
    """Delete any pre-existing hansard-*.json* files so the new build is
    authoritative — otherwise stale per-quarter raw files would survive
    alongside the new per-month / gzipped layout."""
    for p in root.glob("hansard-*.json"):
        if p.name == "hansard-archives.json":
            continue
        p.unlink()
    for p in root.glob("hansard-*.json.gz"):
        p.unlink()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", default="tools/parsed-fragments")
    ap.add_argument("--root", default=".",
                    help="Repo root where hansard-*.json shards land")
    ap.add_argument("--rolling-days", type=int, default=90)
    ap.add_argument("--live-days", type=int, default=730,
                    help="Quarters at or after (today - live_days) split per-month; "
                         "earlier quarters go into the gzipped archive")
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
    live_cutoff = today - dt.timedelta(days=args.live_days)
    live_cutoff_qid = quarter_id(live_cutoff)

    # Bucket: by quarter, plus a separate rolling bucket. The current
    # quarter rides only in rolling so we don't ship a half-finished
    # quarterly archive that pretends to be complete.
    by_quarter: dict[str, list[dict]] = defaultdict(list)
    rolling: list[dict] = []
    current_qid = quarter_id(today)
    for c in contribs:
        try:
            d = dt.date.fromisoformat(c.get("date", ""))
        except ValueError:
            continue
        qid = quarter_id(d)
        if qid != current_qid:
            by_quarter[qid].append(c)
        if d >= rolling_cutoff:
            rolling.append(c)

    clear_existing_shards(out_root)

    shards_meta: list[dict] = []

    for qid in sorted(by_quarter):
        items = by_quarter[qid]
        if qid >= live_cutoff_qid:
            # Live tier: split per-month, raw JSON.
            by_month: dict[str, list[dict]] = defaultdict(list)
            for c in items:
                mkey = c.get("date", "")[:7]
                if mkey:
                    by_month[mkey].append(c)
            for mkey in sorted(by_month):
                m_items = by_month[mkey]
                path = out_root / f"hansard-{mkey}.json"
                write_raw(path, m_items)
                start, end = month_bounds(mkey)
                shards_meta.append({
                    "url":   path.name,
                    "from":  start.isoformat(),
                    "to":    end.isoformat(),
                    "count": len(m_items),
                })
                print(f"  wrote {path.name}: {len(m_items)} contributions")
        else:
            # Archive tier: one gzipped file per quarter.
            path = out_root / f"hansard-{qid}.json.gz"
            write_gzipped(path, items)
            start, end = quarter_bounds(qid)
            shards_meta.append({
                "url":        path.name,
                "from":       start.isoformat(),
                "to":         end.isoformat(),
                "count":      len(items),
                "compressed": True,
            })
            print(f"  wrote {path.name}: {len(items)} contributions (gz)")

    # Rolling shard (always raw, always present)
    rolling_path = out_root / "hansard-rolling.json"
    write_raw(rolling_path, rolling)
    print(f"  wrote {rolling_path.name}: {len(rolling)} contributions "
          f"(rolling {args.rolling_days}d)")

    manifest = {
        "as_of":   today.isoformat(),
        "rolling": {
            "url":   rolling_path.name,
            "from":  rolling_cutoff.isoformat(),
            "to":    today.isoformat(),
            "count": len(rolling),
        } if rolling else None,
        "shards":  shards_meta,
    }
    manifest_path = out_root / "hansard-archives.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    live = [s for s in shards_meta if not s.get("compressed")]
    arch = [s for s in shards_meta if s.get("compressed")]
    print(f"\n  manifest: {len(live)} live + {len(arch)} archived shards + rolling")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
