# House US — exploration prompt

Copy the section below into a fresh Claude Code session. It's
self-contained.

---

I'm Chris Moran, an editor at the Guardian. I'm not a programmer — I
build through conversation with Claude Code, in plain English. My
voice and working preferences are in `~/.claude/CLAUDE.md`; please
read that first.

I've already built two versions of a tool called **House**. Both are
search surfaces over parliamentary records, built for journalists.
Same UI on purpose; different plumbing underneath because the source
data is different.

- **House UK** — `https://github.com/peter-guillam123/House`. Lives
  at `peter-guillam123.github.io/House/`. UK Parliament publishes
  live, CORS-friendly APIs (Hansard, Questions & Statements, Members,
  Committees), so House UK is a thin static frontend that proxies
  through a small Cloudflare Worker.
- **House AU** — `https://github.com/peter-guillam123/house-au`.
  Lives at `peter-guillam123.github.io/house-au/`. AU has no
  equivalent API, so House AU is a fat-publish-time pipeline: a
  nightly GitHub Action walks aph.gov.au, fetches contribution JSON
  via parliament's own internal transcript endpoint, parses it into
  shards, commits them back to the repo. Browser searches the static
  shards client-side.

There's a public explainer of how the two relate at
`peter-guillam123.github.io/house-au/under-the-hood.html`. **Read
that page in full before you start** — it's the most concise summary
of the architectural split between the two and the editorial
reasoning behind each. Also worth scanning `house-au/about.html`,
which has a diary tracking the AU build's decisions.

**The task**: explore how a US version might work. Not build it
yet — explore. I want a feasibility memo, in my voice (sentence
case, hedged tactically, concrete specifics, push back where I'm
wrong), covering:

1. **What the US data landscape actually offers**, vs UK and AU.
   Congress.gov API, GovInfo (Congressional Record + Committee
   Hearings), C-SPAN, OpenStates, ProPublica's old endpoints,
   anything else relevant. CORS-friendly? Rate-limited? Free?
2. **What surfaces a US version would naturally have** — and
   crucially, which ones it wouldn't. The single most important
   lesson from the AU build is that I tried to feature-match UK
   for too long, then realised AU's source had different shape, and
   committee transcripts in AU weren't worth the dig. The US
   answer probably isn't "Search + Deep Dive + Estimates" — it
   might be Search + Deep Dive + Committee Hearings + Lobbying.
   Or something else. **Don't assume; investigate.**
3. **What's the US-specific "killer surface"** that doesn't have a
   UK or AU equivalent? AU's was Senate Estimates. US candidates
   I'd consider: committee hearing witness search (Big Tech CEOs
   testifying, etc.), lobbying disclosure (LDA filings by
   registrant / client / issue), campaign finance (FEC), Federal
   Register notices (executive-branch rulemaking pipeline). One of
   these probably deserves a dedicated tab. Tell me which and why.
4. **Cost shape** — would US sit closer to UK (live API thin
   client) or AU (fat publish-time pipeline)? Be specific about
   which endpoints support live use vs which need harvest.
5. **Editorial use cases** that drove the shape of UK and AU.
   What does a Guardian US journalist actually need on deadline?
   Senate hearing testimony, committee witness statements
   delivered hours before the actual hearing transcript appears,
   FOIA-released documents, lobbying disclosures, legislative
   tracking? The data choices follow the use cases.
6. **Open licence questions**. UK has the Open Parliament Licence
   v3.0 (permissive). AU has CC-BY-NC-ND 4.0 (we navigated this
   via internal-tool framing — counsel were relaxed). US federal
   government works are public domain (no copyright on US
   government output), which materially changes the calculus. But
   third-party data (LobbyDisclosure filings, individual senator
   websites, etc.) may have separate restrictions. Worth flagging.

**Constraints to carry over**:

- The visual identity and editorial register travel directly.
  Same fonts (Source Serif 4 / Inter / JetBrains Mono), same
  cream paper / near-black ink palette, same restrained accent
  approach. US's accent is **Federal navy `#1d2f5e`** with the
  region tag `US` styled in the same mono-caps way as `UK` and
  `AU` are now. Brand kit + favicon + OG card already exist at
  `peter-guillam123.github.io/house-au/regions/` if you want a
  reference.
- Internal-newsroom-tool framing, hosted on my personal GitHub
  initially with the same expectation that it eventually moves
  to the Guardian stack behind authentication.
- Don't fall for false feature parity with UK or AU. The
  honest test is: what does the US source actually support, and
  what does that mean a US journalist could do?

**What I want from you in this first session**:

1. Read House AU's `under-the-hood.html` and `about.html` so you
   understand what's already been built and why.
2. Spend an hour or two doing real research on US data sources —
   actual API docs, actual sample queries, actual rate limits.
   Don't rely on memory. Verify what works in 2026, not what was
   true in 2020.
3. Come back with a feasibility memo in my voice: surfaces I'd
   propose, the cost shape, the killer US-only surface, the
   licence picture, and an honest finishing-run scope. **Push
   back where my framing's wrong** — that's where the value is.
4. **Don't start coding.** This is the planning conversation.
   Building comes later, in a separate session, after I've read
   your memo and we've argued about it.

If you need to ask me something to clarify scope before doing the
research, ask once early and then run.
