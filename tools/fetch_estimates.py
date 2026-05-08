#!/usr/bin/env python3
"""
Fetch Senate Estimates fragment JSON for every session in the
discovery file. Same shape as fetch_fragments.py for chamber Hansard,
but the bid prefix is committees/estimate/<ref>/ instead of
chamber/hansardr/ or chamber/hansards/.

Per session: one HTML fetch to enumerate sids from the TOC, then one
JSON fetch per sid via /api/hansard/transcript?id=<bid><sid>. Output
is cached as tools/raw-estimates/<ref>.json with all sids inside —
mirrors the chamber per-day file pattern. Resumable: existing files
are skipped unless --force is passed.

Estimates fragments are larger than chamber turns (one fragment can
cover an entire portfolio's grilling for a day, 30KB-1MB+ of HTML),
so the per-request sleep is a hair longer to be polite.

Usage:
    python3 tools/fetch_estimates.py
    python3 tools/fetch_estimates.py --workers 4 --since 2025-10-01
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import re
import socket
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

HOST = "www.aph.gov.au"
DISPLAY_PATH    = "/Parliamentary_Business/Hansard/Hansard_Display"
TRANSCRIPT_PATH = "/api/hansard/transcript"

SID_RE = re.compile(r'data-sid="(\d{4})"')

# --- per-thread persistent HTTPS connection (same pattern as fetch_fragments) ---
_thread_local = threading.local()


def _conn() -> http.client.HTTPSConnection:
    c = getattr(_thread_local, "conn", None)
    if c is None:
        c = http.client.HTTPSConnection(HOST, timeout=60)  # bigger timeout, payloads run large
        _thread_local.conn = c
    return c


def _reset_conn() -> None:
    c = getattr(_thread_local, "conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
    _thread_local.conn = http.client.HTTPSConnection(HOST, timeout=60)


def http_get(path: str, params: dict | None = None, retries: int = 3) -> bytes:
    if params:
        path = path + "?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": UA,
        "Accept":     "application/json, text/html, */*",
        "Connection": "keep-alive",
    }
    last_err: Exception | None = None
    for attempt in range(retries):
        c = _conn()
        try:
            c.request("GET", path, headers=headers)
            resp = c.getresponse()
            body = resp.read()
            if resp.status == 200:
                return body
            last_err = RuntimeError(f"HTTP {resp.status} {resp.reason}")
        except (http.client.HTTPException, ConnectionError, socket.error, OSError) as e:
            last_err = e
        _reset_conn()
        time.sleep(1.5 ** attempt)
    raise RuntimeError(f"{path}: {last_err}")


def discover_sids(bid_path: str) -> list[str]:
    """Fetch the Hansard_Display page and return all section IDs."""
    html = http_get(DISPLAY_PATH, {"bid": bid_path, "sid": "0000"}).decode(
        "utf-8", errors="replace"
    )
    return sorted(set(SID_RE.findall(html)))


def fetch_fragment(bid_path: str, sid: str) -> dict:
    body = http_get(TRANSCRIPT_PATH, {"id": f"{bid_path}{sid}"})
    return json.loads(body)


def harvest_session(session: dict, out_dir: Path, force: bool = False) -> dict:
    """Fetch every sid for one Estimates session, write a per-session file."""
    ref = session["ref"]
    bid = session["bid"]
    out_path = out_dir / f"{ref}.json"
    if out_path.exists() and not force:
        return {"session": session, "status": "skipped", "fragments": 0}

    out_path.parent.mkdir(parents=True, exist_ok=True)

    sids = discover_sids(bid)
    fragments = []
    for sid in sids:
        try:
            fragments.append({"sid": sid, "data": fetch_fragment(bid, sid)})
        except Exception as e:
            fragments.append({"sid": sid, "error": str(e)})
        time.sleep(0.05)  # polite floor; Estimates fragments are bigger

    payload = {
        "ref":            ref,
        "bid":            bid,
        "round_id":       session.get("round_id", ""),
        "round":          session.get("round", ""),
        "date":           session.get("date", ""),
        "committee":      session.get("committee", ""),
        "committee_slug": session.get("committee_slug", ""),
        "fetched_at":     dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "fragments":      fragments,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "session":   session,
        "status":    "ok",
        "fragments": sum(1 for f in fragments if "data" in f),
        "errors":    sum(1 for f in fragments if "error" in f),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discovery", default="tools/estimates-ids.json")
    ap.add_argument("--out", default="tools/raw-estimates")
    ap.add_argument("--since", default=None,
                    help="Only harvest sessions on or after this ISO date")
    ap.add_argument("--workers", type=int, default=4,
                    help="Concurrent session workers (default 4 — Estimates "
                         "fragments are larger than chamber turns)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    discovery = json.loads(Path(args.discovery).read_text())
    sessions = discovery["sessions"]
    if args.since:
        cutoff = dt.date.fromisoformat(args.since)
        sessions = [s for s in sessions
                    if s["date"] and dt.date.fromisoformat(s["date"]) >= cutoff]
    if args.limit:
        sessions = sessions[: args.limit]

    out_dir = Path(args.out)
    print(f"Harvesting {len(sessions)} Estimates sessions → {out_dir} "
          f"({args.workers} workers)")

    ok = skipped = failed = total_fragments = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(harvest_session, s, out_dir, args.force): s
                   for s in sessions}
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                failed += 1
                print(f"  FAIL  {s['committee_slug']:8} {s['date']} "
                      f"({s['ref']}): {e}")
                continue
            if res["status"] == "skipped":
                skipped += 1
                continue
            ok += 1
            total_fragments += res["fragments"]
            tag = "OK  " if res.get("errors", 0) == 0 else "OK* "
            print(f"  {tag}{s['committee_slug']:8} {s['date']} "
                  f"({s['ref']}): {res['fragments']} fragments"
                  + (f", {res['errors']} errors" if res.get("errors") else ""))

    print(f"\nDone. ok={ok} skipped={skipped} failed={failed} "
          f"fragments={total_fragments}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
