# Known limitations

- **Website coverage is bounded by candidate generation.** Without a search API (none can be used without
  entrant-tied keys), candidates are the registry homepage, the registry e-mail domain and name-derived `.no`/`.com`
  domains. Companies whose site uses an unrelated brand domain are usually `not_available`. On our frozen random
  dev sets about 7–10% of companies get a verified website (the registry lists one for ~14%, many of which are
  management companies, parents or dead).
- **Strict identity gate trades recall for precision.** Sites without the organisation number or the full legal name
  stay `ambiguous` (candidate and reason recorded in `attempts`). JavaScript-only sites (no server-rendered text) are
  not rendered and stay `ambiguous`. A group or chain site that the register itself lists as a subsidiary's homepage
  (and that names the subsidiary) is still accepted; audits count some of these as the group's site, not the
  company's (about 1 in 60 name-based matches; e.g. a global group site, an office community of several firms).
  Posts and profiles on such a site are dropped when the site lists several group companies' numbers.
- **Hiring** comes from NAV arbeidsplassen (the official feed; ~9,300 active ads in a 120-day index). Ads published
  under an organisation number that is neither the company nor one of its registered subunits are not attributed
  (e.g. a parent or staffing agency advertising for it). Careers pages on verified sites add free-text postings
  (a position heading plus an apply cue, deadline not passed); ads linked to FINN are read once (robots.txt allows
  job ads) and dropped when FINN says the deadline has passed; ads on other boards are published from the link title
  without fetching the board, so their expiry is not re-checked. Staffing firms' NAV ads placed "on behalf of" a client
  are published under the staffing firm (it is the NAV employer). Careers pages that say nothing is open
  are cited on the `not_available` claim. Most randomly drawn companies have no
  active ads, which is reported as `not_available` (checked), not as zero.
- **Dated activity** from the verified site needs an RSS/Atom feed, the WordPress REST API, a news section with
  visible dates, or article pages with a publish date (JSON-LD, meta, `<time>`, "Publisert ..." labels). Theme demo
  posts, render-time stamps and undated items are dropped; sitemap `lastmod` only orders candidates. A "last updated"
  date is marked `date_basis: last_updated`. Most verified sites (~65%) carry no dated posts at all.
- **Social profiles** are links found on the verified site; the platforms are never fetched, so follower counts,
  posts and profile liveness are not verified. A link whose handle does not match the company is published only when
  the site's own structured data lists it, or it is the site's single numeric Facebook page shown on most pages.
- **Rate limits**: some site platforms (notably Wix) answer automated clients with HTTP 429 under load; such a site is
  reported `failed` for that run (one polite retry), not as absent.
- **Annual accounts**: the open Regnskapsregisteret API returns the latest filings (up to three records); older
  history and PDF copies are not fetched (the copies endpoint is rate-limited to ~30 requests/min).
- **Official announcements (kunngjøringer)** are not used: `w2.brreg.no/kunngjoring/` is disallowed by robots.txt and
  the XML subscription needs a signed agreement.
- **Synthesis** is a deterministic template over supported claims (no model). It names unknowns and material
  changes but does not interpret strategy or market position.
- **Runtime** depends on the NAV feed index build (~3–4 minutes, sequential paging) and on slow company sites;
  per-company caps and a global deadline guarantee one envelope per input regardless.
