# House AU

A search tool for federal Australian parliamentary proceedings,
modelled on [House (UK)](https://github.com/peter-guillam123/House) but
adapted to a different democratic shape and a very different data
landscape.

**Internal newsroom tool, scaffold stage.** Counsel are relaxed about
the AU CC-BY-NC-ND licence given the internal-tool framing. The
expectation is that this moves to the Guardian stack behind
authentication once it works.

## What this will be

Five search surfaces over Hansard and ParlInfo:

1. **Search** — single field across spoken contributions, questions on
   notice, ministerial statements, committee Hansards, and Senate
   Estimates.
2. **Deep Dive** — party-stacked monthly timeline for a single term.
3. **Committees** — committees browse and drill-in.
4. **Transcripts** — full-text search of committee oral evidence.
5. **Estimates** *(headline feature)* — Senate Estimates with filters
   by round, committee, portfolio, department and witness. The bit
   that doesn't exist in House UK because there's no UK equivalent.

## Why the architecture differs from House UK

UK Parliament has a live, CORS-friendly API. aph.gov.au has no
equivalent. So House AU is built on a *daily-harvest static-index*
pattern: a nightly GitHub Action walks the latest sitting days,
downloads each Hansard XML, parses it into speaker-attributed
segments, and commits a JSON index back to the repo. The browser
fetches that JSON from GitHub Pages and searches it client-side. No
Cloudflare Worker, no live API dependency, no CORS proxy.

Trade-off: data is up to one day stale. Acceptable for the internal
audience — Hansard itself runs ~24 hours behind the chamber.

## Status

| Piece | State |
|---|---|
| Visual scaffold (palette, layout, typography) | done |
| Data layer | stubbed — every search returns empty |
| Harvester | not yet written |
| Five surfaces | scaffolded from House UK, copy not yet localised |
| Senate Estimates surface | not yet started |

## Local dev

```sh
python3 -m http.server 8000
# open http://localhost:8000
```

In scaffold mode every page loads but searches return nothing — that's
expected. The console logs a one-line notice saying scaffold mode is
active.

## Design

Identical to House UK in layout, typography and rhythm. Two palette
swaps for the Australian context:

- The oxblood accent (`#6b1f24`) is replaced with **Pantone 348C green
  (`#00843d`)** — used for focused states, active filters, link hovers,
  the wordmark mark. Contrast against cream paper is ~5.3:1, passes
  WCAG AA for normal text.
- The soft aged-marker highlight is replaced with **Pantone 116C gold
  (`#ffcd00`)** — but **only** as the search-match highlight fill. Gold
  on cream is 1.3:1, totally illegible as foreground. So gold is used
  as a background fill behind dark text and never as text/link colour.

## Licence

Hansard and other Parliament of Australia material is published under
CC-BY-NC-ND 4.0. This tool is for internal newsroom use, not public
republication, so we're not making a derivative work for the open
internet. If/when it moves to the Guardian stack the same internal-tool
framing applies.
