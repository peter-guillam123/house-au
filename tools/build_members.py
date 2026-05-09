#!/usr/bin/env python3
"""
Walk the parsed contributions for unique MPIDs, then fetch each
parliamentarian's profile page and extract party + chamber + display
name. Write the result to members.json at the repo root for the
frontend to load on demand.

aph.gov.au exposes a clean profile page per MPID at
/Senators_and_Members/Parliamentarian?MPID={MPID}, with a structured
<dt>/<dd> pair list including:

  Party     → 'Australian Labor Party' / 'Liberal Party of Australia' / …
  Chamber   → 'House of Representatives' / 'Senate'

Plus an H1 with the full name + honorific ('Hon Tony Burke MP').

There's no JSON variant of this page that returns 200, so HTML
scraping it is. Each fetch is one persistent-connection request. ~325
parliamentarians across the 2020-present window → ~5 minutes of polite
fetching.

Output:
    members.json
    {
      "as_of": "2026-05-08",
      "members": {
        "DYW": {
          "name":    "Hon Tony Burke MP",
          "party":   "Australian Labor Party",
          "chamber": "House of Representatives"
        },
        ...
      }
    }
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

HOST = "www.aph.gov.au"
PROFILE_PATH = "/Senators_and_Members/Parliamentarian"

# --- per-thread persistent HTTPS connection (same pattern as fetch_fragments) ---
_thread_local = threading.local()


def _conn() -> http.client.HTTPSConnection:
    c = getattr(_thread_local, "conn", None)
    if c is None:
        c = http.client.HTTPSConnection(HOST, timeout=30)
        _thread_local.conn = c
    return c


def _reset_conn() -> None:
    c = getattr(_thread_local, "conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
    _thread_local.conn = http.client.HTTPSConnection(HOST, timeout=30)


def http_get(path: str, retries: int = 3) -> bytes:
    headers = {"User-Agent": UA, "Connection": "keep-alive"}
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


# --- HTML extractors ---

DT_DD_RE = re.compile(r"<dt>([^<]+)</dt>\s*<dd>([^<]+)</dd>", re.S)
H1_RE = re.compile(r"<h1[^>]*>([^<]+)</h1>", re.S)


def parse_profile(html: str) -> dict | None:
    pairs = {k.strip().rstrip(":"): v.strip()
             for k, v in DT_DD_RE.findall(html)}
    party = pairs.get("Party")
    chamber = pairs.get("Chamber")
    if not party and not chamber:
        return None
    name_m = H1_RE.search(html)
    name = name_m.group(1).strip() if name_m else ""
    return {
        "name":    name,
        "party":   party or "",
        "chamber": chamber or "",
    }


def fetch_member(mpid: str) -> tuple[str, dict | None]:
    """Returns (mpid, parsed-or-None)."""
    try:
        body = http_get(f"{PROFILE_PATH}?MPID={mpid}")
        info = parse_profile(body.decode("utf-8", errors="replace"))
        return mpid, info
    except Exception as e:
        return mpid, {"_error": str(e)}


def collect_mpids(parsed_root: Path) -> list[str]:
    """Distinct MPIDs across every parsed contribution file."""
    seen = set()
    for path in parsed_root.rglob("*.json"):
        try:
            for c in json.loads(path.read_text()):
                mpid = c.get("speakerId")
                if mpid:
                    seen.add(mpid)
        except json.JSONDecodeError:
            continue
    return sorted(seen)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", default="tools/parsed-fragments")
    ap.add_argument("--out", default="members.json")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args(argv)

    parsed_root = Path(args.parsed)
    if not parsed_root.exists():
        print(f"No parsed contributions at {parsed_root}", file=sys.stderr)
        return 1

    mpids = collect_mpids(parsed_root)
    print(f"Fetching profiles for {len(mpids)} MPIDs ({args.workers} workers)")

    members: dict[str, dict] = {}
    failed = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(fetch_member, m): m for m in mpids}
        done = 0
        for fut in as_completed(futures):
            mpid, info = fut.result()
            done += 1
            if info is None:
                # 200 but no Party/Chamber found — likely a non-parliamentarian
                # page redirect or someone whose profile is sparse.
                failed += 1
                if done % 25 == 0 or done == len(mpids):
                    print(f"  [{done}/{len(mpids)}] (no profile fields for {mpid})")
                continue
            if "_error" in info:
                failed += 1
                print(f"  FAIL {mpid}: {info['_error']}")
                continue
            members[mpid] = info
            if done % 25 == 0 or done == len(mpids):
                print(f"  [{done}/{len(mpids)}] {mpid}: {info['party']!r}")

    elapsed = time.time() - started
    print(f"\n  ok={len(members)}  failed={failed}  in {elapsed:.0f}s")

    # Sort MPID keys so the on-disk file is deterministic between runs.
    # Python dict iteration order is insertion-order, but ThreadPoolExecutor's
    # as_completed order is non-deterministic, so without sorting the same
    # parties produce a 600-line reorder churn in git every nightly run.
    payload = {
        "as_of":   dt.date.today().isoformat(),
        "members": dict(sorted(members.items())),
    }
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
