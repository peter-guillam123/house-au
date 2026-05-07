#!/usr/bin/env python3
"""
Turn raw fragment JSON (one file per sitting day, written by
fetch_fragments.py) into normalised contribution segments suitable
for client-side search.

Each fragment becomes one contribution row with:

    {
      "id":          "<bid><sid>",         # unique
      "date":        "YYYY-MM-DD",
      "chamber":     "reps" | "senate",
      "source":      "Spoken" | "QuestionWithoutNotice"
                   | "QuestionOnNotice" | "Statement",
      "speakerName": "Burke, Tony MP",
      "shortName":   "Tony Burke",
      "speakerId":   "DYW",                # parliamentarian MPID
      "electorate":  "Watson",
      "minTitle":    "Minister for the Arts, ...",
      "title":       "BUSINESS - Days and Hours of Meeting",
      "context":     "BUSINESS",
      "fullText":    "I move: That so much of the standing...",
      "videoLink":   "...",
      "link":        "https://www.aph.gov.au/Parliamentary_Business/...",
    }

Output: per-day JSON files in tools/parsed-fragments/<chamber>/.
The shape mirrors UK House's contribution shape closely enough that
the frontend code can reuse most of its rendering logic.

Usage:
    python3 tools/parse_fragments.py
    python3 tools/parse_fragments.py --raw tools/raw-fragments --out tools/parsed-fragments
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

CHAMBER_TO_SLUG = {
    "House of Representatives": "reps",
    "Senate": "senate",
}

DEEP_LINK_BASE = "https://www.aph.gov.au/Parliamentary_Business/Hansard/Hansard_Display"


class TalkTextExtractor(HTMLParser):
    """Walk TalkText HTML once, pulling out:

      - plain text (paragraph-broken)
      - first MPID found in <a href="...?MPID=XYZ">
      - first electorate / ministerial title / member-speech name
        in <span class="HPS-...">
    """

    SPAN_FIELDS = {
        "HPS-Electorate":          "electorate",
        "HPS-MinisterialTitles":   "minTitle",
        "HPS-MemberSpeech":        "memberSpeech",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.fields: dict[str, str] = {}
        self._capture_field: str | None = None
        self._buf: list[str] = []
        self._block_tags = {"p", "div", "br", "li"}
        self._mpid: str | None = None

    def handle_starttag(self, tag: str, attrs):
        attr = dict(attrs)
        if tag == "a" and self._mpid is None:
            href = attr.get("href", "")
            m = re.search(r"MPID=([A-Z0-9]+)", href)
            if m:
                self._mpid = m.group(1)
        if tag == "span":
            cls = attr.get("class", "")
            for css_cls, field in self.SPAN_FIELDS.items():
                if css_cls in cls and field not in self.fields:
                    self._capture_field = field
                    self._buf = []
                    return
        if tag in self._block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag == "span" and self._capture_field:
            self.fields[self._capture_field] = "".join(self._buf).strip()
            self._capture_field = None
            self._buf = []
        if tag in self._block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if self._capture_field is not None:
            self._buf.append(data)
        self.parts.append(data)

    @property
    def text(self) -> str:
        raw = "".join(self.parts)
        # Normalise whitespace: collapse runs of whitespace except
        # paragraph breaks (≥ 2 newlines).
        raw = re.sub(r"[ \t\r]+", " ", raw)
        raw = re.sub(r"\n[ \t]*", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()

    @property
    def mpid(self) -> str | None:
        return self._mpid


def extract_talk(talk_html: str) -> dict:
    if not talk_html:
        return {"text": "", "fields": {}, "mpid": None}
    p = TalkTextExtractor()
    try:
        p.feed(talk_html)
        p.close()
    except Exception:
        # If something exotic in the HTML breaks the parser, fall back
        # to a regex-stripped text + regex MPID — better than dropping
        # the contribution.
        text = re.sub(r"<[^>]+>", " ", talk_html)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        m = re.search(r"MPID=([A-Z0-9]+)", talk_html)
        return {"text": text, "fields": {}, "mpid": m.group(1) if m else None}
    return {"text": p.text, "fields": p.fields, "mpid": p.mpid}


def short_name(speaker: str | None) -> str:
    """'Burke, Tony MP' → 'Tony Burke'.

    Fall back to whatever we got if the format doesn't match — Senate
    uses 'Cash, Michaelia, Senator', etc. We strip the trailing role
    tag and flip last-comma-first."""
    if not speaker:
        return ""
    s = speaker.strip().rstrip(",")
    parts = [p.strip() for p in s.split(",")]
    if len(parts) >= 2:
        last = parts[0]
        first_and_role = parts[1].split()
        # Drop trailing role tokens (MP, Senator)
        first = " ".join(t for t in first_and_role if t.upper() not in {"MP", "SENATOR"})
        return f"{first} {last}".strip()
    return s


def classify_source(main_title: str, context: str) -> str:
    """Heuristic source-type classification mirroring UK House's
    spoken/written/wq/ws split, adapted to AU debate naming."""
    blob = f"{main_title or ''} {context or ''}".upper()
    if "QUESTIONS WITHOUT NOTICE" in blob:
        return "QuestionWithoutNotice"
    if "QUESTIONS ON NOTICE" in blob or "QUESTIONS IN WRITING" in blob:
        return "QuestionOnNotice"
    if "MINISTERIAL STATEMENT" in blob or "STATEMENTS BY MEMBERS" in blob:
        return "Statement"
    return "Spoken"


def parse_date(au_date: str | None) -> str:
    """'4/02/2020' → '2020-02-04'. Returns '' if unparseable."""
    if not au_date:
        return ""
    try:
        return dt.datetime.strptime(au_date.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return ""


def deep_link(bid: str, sid: str) -> str:
    return f"{DEEP_LINK_BASE}?bid={bid}&sid={sid}"


def parse_one_file(raw_path: Path) -> list[dict]:
    raw = json.loads(raw_path.read_text())
    chamber = raw["chamber"]
    contributions: list[dict] = []
    for frag in raw["fragments"]:
        if "data" not in frag:
            continue  # skip errored fetches
        d = frag["data"]
        sid = frag["sid"]
        bid = raw["bid"]

        talk = extract_talk(d.get("TalkText") or "")
        text = talk["text"]
        if not text:
            # No body — skip TOC-only sections. Keeps the index small.
            continue

        speaker = d.get("Speaker") or ""
        contributions.append({
            "id":          f"{bid}{sid}",
            "date":        parse_date(d.get("Date")),
            "chamber":     chamber,
            "source":      classify_source(d.get("MainTitle") or "", d.get("Context") or ""),
            "speakerName": speaker,
            "shortName":   short_name(speaker),
            "speakerId":   talk["mpid"] or "",
            "electorate":  talk["fields"].get("electorate", ""),
            "minTitle":    talk["fields"].get("minTitle", ""),
            "title":       d.get("MainTitle") or "",
            "context":     d.get("Context") or "",
            "fullText":    text,
            "videoLink":   d.get("VideoLink") or "",
            "link":        deep_link(bid, sid),
        })
    return contributions


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="tools/raw-fragments")
    ap.add_argument("--out", default="tools/parsed-fragments")
    args = ap.parse_args(argv)

    raw_root = Path(args.raw)
    out_root = Path(args.out)
    if not raw_root.exists():
        print(f"No raw fragments at {raw_root}", file=sys.stderr)
        return 1

    days = total = 0
    for raw_path in sorted(raw_root.rglob("*.json")):
        chamber = raw_path.parent.name
        contributions = parse_one_file(raw_path)
        out_path = out_root / chamber / raw_path.name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(contributions, indent=2) + "\n")
        days += 1
        total += len(contributions)
        print(f"  {chamber:6} {raw_path.stem.split('_')[0]}: "
              f"{len(contributions)} contributions")

    print(f"\nParsed {total} contributions across {days} days → {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
