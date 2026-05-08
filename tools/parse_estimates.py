#!/usr/bin/env python3
"""
Parse raw Estimates fragment JSON into normalised section rows.

The chamber Hansard parser splits TalkText into one row per speaker
turn. Estimates is structured differently — each fragment is a whole
section (department's grilling for one day, e.g. "Department of the
Prime Minister and Cabinet"), with multiple senators asking questions
and multiple witnesses answering. The right editorial unit is the
section, not the individual turn.

So each fragment becomes one section row with:

    - committee, round, date, portfolio + department
    - questioners + responders (semicolon-separated lists from the API)
    - the full plain-text body (for full-text search)
    - the turn-by-turn breakdown (for snippet display + speaker counts)
    - a deep link back to ParlInfo

Output: tools/parsed-estimates/{ref}.json — a list of section rows
mirroring the per-day file pattern from chamber Hansard.

Usage:
    python3 tools/parse_estimates.py
    python3 tools/parse_estimates.py --raw tools/raw-estimates --out tools/parsed-estimates
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

DEEP_LINK_BASE = "https://www.aph.gov.au/Parliamentary_Business/Hansard/Hansard_Display"

MPID_RE = re.compile(r"MPID=([A-Z0-9]+)")


class EstimatesExtractor(HTMLParser):
    """Walks an Estimates TalkText, splitting it into a sequence of
    speaker turns. Each new <a href="...?MPID=NUM"> opens a new turn;
    text between that anchor's </a> and the next MPID anchor is the
    body of that turn.

    Captures alongside each turn:
      - mpid (stable parliamentarian id)
      - name (from HPS-MemberContinuation / HPS-MemberSpeech span)
      - electorate (HPS-Electorate, when present — usually only for MPs)
      - ministerialTitle (HPS-MinisterialTitles — when applicable)
      - timestamp (HPS-Time — sometimes present at turn start)
    """

    SPAN_FIELDS = {
        "HPS-MemberContinuation": "name",
        "HPS-MemberSpeech":       "name",
        "HPS-Electorate":         "electorate",
        "HPS-MinisterialTitles":  "ministerialTitle",
        "HPS-Time":               "time",
    }

    BLOCK_TAGS = {"p", "div", "br", "li"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.turns: list[dict] = []
        self._current: dict | None = None
        # Track depth inside the speaker-anchor — text inside it is
        # the speaker label, not the speech.
        self._anchor_depth = 0
        # Capture state for one of the SPAN_FIELDS targets.
        self._capture_target: str | None = None
        self._capture_buf: list[str] = []

    # --- HTMLParser hooks ---

    def handle_starttag(self, tag: str, attrs):
        attr = dict(attrs)

        if tag == "a":
            href = attr.get("href", "")
            mpid_m = MPID_RE.search(href)
            if mpid_m:
                # New turn boundary.
                self._flush_current()
                self._current = {
                    "mpid":              mpid_m.group(1),
                    "name":              "",
                    "electorate":        "",
                    "ministerialTitle":  "",
                    "time":              "",
                    "_parts":            [],
                }
                self._anchor_depth = 1
                return
            if self._anchor_depth > 0:
                self._anchor_depth += 1

        elif tag == "span" and self._current is not None:
            cls = attr.get("class", "")
            for css_cls, field in self.SPAN_FIELDS.items():
                if css_cls in cls and not self._current[field]:
                    self._capture_target = field
                    self._capture_buf = []
                    return

        elif tag in self.BLOCK_TAGS and self._current is not None and self._anchor_depth == 0:
            self._current["_parts"].append("\n")

    def handle_endtag(self, tag: str):
        if tag == "a":
            if self._anchor_depth > 0:
                self._anchor_depth -= 1
        elif tag == "span" and self._capture_target:
            value = "".join(self._capture_buf).strip()
            if self._current and not self._current[self._capture_target]:
                self._current[self._capture_target] = value
            self._capture_target = None
            self._capture_buf = []
        elif tag in self.BLOCK_TAGS and self._current is not None and self._anchor_depth == 0:
            self._current["_parts"].append("\n")

    def handle_data(self, data: str):
        if self._capture_target is not None:
            self._capture_buf.append(data)
        elif self._current is not None and self._anchor_depth == 0:
            self._current["_parts"].append(data)

    # --- helpers ---

    def _flush_current(self) -> None:
        if not self._current:
            return
        text = self._normalise_text("".join(self._current["_parts"]))
        if text or self._current["name"]:
            # Strip trailing colons from name labels ("Senator HUME:")
            name = self._current["name"].rstrip(":").strip()
            self.turns.append({
                "mpid":             self._current["mpid"],
                "name":             name,
                "electorate":       self._current["electorate"],
                "ministerialTitle": self._current["ministerialTitle"],
                "time":             self._current["time"],
                "text":             text,
            })
        self._current = None

    def close(self) -> None:
        self._flush_current()
        super().close()

    @staticmethod
    def _normalise_text(s: str) -> str:
        s = re.sub(r"[ \t\r]+", " ", s)
        s = re.sub(r"\n[ \t]*", "\n", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()


def extract_section_turns(talk_html: str) -> list[dict]:
    if not talk_html:
        return []
    p = EstimatesExtractor()
    try:
        p.feed(talk_html)
        p.close()
    except Exception:
        # Fall back to a regex strip rather than dropping the section.
        text = re.sub(r"<[^>]+>", " ", talk_html)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return [{"mpid": "", "name": "", "electorate": "",
                 "ministerialTitle": "", "time": "", "text": text}]
    return p.turns


# --- splitting Questioner/Responder string lists ---
# The API returns these as semicolon-separated, sometimes truncated mid-name
# ("Sheldon, Sen T..." in one of the samples). We split, strip, and drop
# anything ending in a bare ellipsis to avoid useless partial chips.

def split_role_list(s: str | None) -> list[str]:
    if not s:
        return []
    out = []
    for part in s.split(";"):
        v = part.strip()
        if not v:
            continue
        if v.endswith("..."):
            continue
        out.append(v)
    return out


# --- date normalisation ---

def parse_date(au_date: str | None) -> str:
    if not au_date:
        return ""
    try:
        return dt.datetime.strptime(au_date.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return ""


# --- section row construction ---

def deep_link(bid: str, sid: str) -> str:
    return f"{DEEP_LINK_BASE}?bid={bid}&sid={sid}"


def section_kind(title: str) -> str:
    """Heuristic: ALL-CAPS section titles ("PARLIAMENTARY DEPARTMENTS")
    are portfolio/header openers between department blocks. Mixed-case
    titles ("Department of the Senate") are the actual department
    grillings — the journalistically interesting bit."""
    if not title:
        return "unknown"
    if re.fullmatch(r"[A-Z0-9 :&'/\-,()]+", title):
        return "portfolio_header"
    return "department"


def parse_one_session_file(raw_path: Path) -> list[dict]:
    raw = json.loads(raw_path.read_text())
    rows: list[dict] = []
    for frag in raw["fragments"]:
        if "data" not in frag:
            continue
        d = frag["data"]
        sid = frag["sid"]
        bid = raw["bid"]

        title = (d.get("Title") or "").strip()
        # Skip the always-empty "table of contents" fragment if any
        talk = d.get("TalkText") or ""
        if not talk:
            continue

        turns = extract_section_turns(talk)
        full_text = "\n\n".join(t["text"] for t in turns if t["text"]).strip()
        if not full_text:
            continue

        rows.append({
            "id":              f"{bid}{sid}",
            "ref":             raw["ref"],
            "sid":             sid,
            "date":            raw.get("date") or parse_date(d.get("Date")),
            "round":           raw.get("round", ""),
            "round_id":        raw.get("round_id", ""),
            "committee":       raw.get("committee", ""),
            "committee_slug":  raw.get("committee_slug", ""),
            "section":         title,
            "section_kind":    section_kind(title),
            "questioners":     split_role_list(d.get("Questioner")),
            "responders":      split_role_list(d.get("Responder")),
            "fullText":        full_text,
            "turns":           turns,
            "link":            deep_link(bid, sid),
            "videoLink":       d.get("VideoLink") or "",
        })
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="tools/raw-estimates")
    ap.add_argument("--out", default="tools/parsed-estimates")
    args = ap.parse_args(argv)

    raw_root = Path(args.raw)
    out_root = Path(args.out)
    if not raw_root.exists():
        print(f"No raw Estimates at {raw_root}", file=sys.stderr)
        return 1

    sessions = total_rows = total_turns = 0
    for raw_path in sorted(raw_root.glob("*.json")):
        rows = parse_one_session_file(raw_path)
        out_path = out_root / raw_path.name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rows, indent=2) + "\n")
        sessions += 1
        total_rows += len(rows)
        total_turns += sum(len(r["turns"]) for r in rows)
        date = rows[0]["date"] if rows else "—"
        slug = rows[0]["committee_slug"] if rows else "—"
        print(f"  {slug:8} {date} ({raw_path.stem}): {len(rows)} sections, "
              f"{sum(len(r['turns']) for r in rows)} turns")

    print(f"\nParsed {total_rows} sections ({total_turns} turns) "
          f"across {sessions} sessions → {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
