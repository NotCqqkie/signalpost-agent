# Signalpost agent — exact-identity Norwegian company research

Give it Norwegian organisation numbers; it returns **exactly one terminal JSON envelope per input**: legal identity
and public brand, latest filed annual accounts, leadership and registered workplaces, a verified company website and
company-owned profiles, hiring (NAV) and dated public activity, every claim with source URL, retrieval time, content
hash, quoted span and reporting period, honest availability states, refresh diffs against a previous run and a
deterministic plain-English summary (by default `summary.text` is the cited synthesis brief; `SIGNALPOST_SUMMARY_FROM_SYNTHESIS=0`
keeps the short template) and a structured `synthesis` section (sentences with cited claim ids, explicit
unknowns, changes) that cite only supported claims.

No API keys, no accounts, no LLM, no paid services: **third-party cost $0 per run.**
Built on the Builderr Signalpost starter kit (MIT, see `LICENSE`; original README in `docs/STARTER-README.md`).

## Run (one command)

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). Dependencies are pinned in `uv.lock`.

```bash
uv sync --frozen
uv run python scripts/run_competition_batch.py --organisations companies.jsonl --output out/envelopes.jsonl
```

Without uv: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt` (hash-pinned, exported from
`uv.lock`; Windows: `.venv\Scripts\pip`), then `.venv/bin/python scripts/run_competition_batch.py ...` with the same flags.

- `companies.jsonl`: one `{"organisation_number": "923609016"}` per line (a JSON list or a plain list of numbers
  also works). Duplicates are collapsed, malformed rows are reported in the run report, nothing aborts the batch.
- Output: `out/envelopes.jsonl` (one envelope per unique input, input order) and `out/envelopes.report.json`
  (counts, states, per-family coverage, requests, runtime, NAV index status, cost = 0).
- **Company site: `out/site/index.html`**, written automatically next to the envelopes (open it in any browser;
  self-contained, no external requests, works from `file://` and when hosted as static files). Find a company by
  name, organisation number or town (filters: all five data areas, website, hiring, town, sector, type); compare two
  side by side (`#/compare/<org>/<org>`, shareable); verify any number or claim in one click (source URL, retrieval
  time, reporting period, quoted span); a deterministic "Ask Signalpost" panel answers common questions only from the
  profile's cited facts and says "not found" otherwise. Desktop and phone (375 px). `--site DIR` writes it elsewhere,
  `--site none` skips it; `scripts/build_viewer.py envelopes.jsonl DIR` rebuilds it from any envelopes file.
- Optional flags: `--bulk brreg-enheter.csv` (the frozen BRREG Enhetsregisteret bulk snapshot; without it the live
  register API is used), `--previous earlier-envelopes.jsonl` (refresh: typed material changes, failed refreshes
  keep the last supported value marked `stale`), `--viewer out/viewer.html` (older single-file
  viewer, kept for compatibility), `--time-budget-seconds` (default 5400; every input still gets an
  envelope if the budget runs out), `--workers` (default 12).

Refresh against a previous output: run the same command with `--previous old/envelopes.jsonl --output new/envelopes.jsonl`.
Each envelope's `changes` lists typed material changes (old and new value, both with evidence), `refresh` gives the
previous run id, carried-forward claims and the number of material changes; a value that could not be re-checked keeps
its last supported value marked `stale`. A company with no previous envelope says `"first_snapshot": true` and its
summary states that this is the first snapshot.

Smoke test (100 companies, as submitted): see `smoke/` — `smoke/smoke-report.json`, `smoke/envelopes.jsonl`,
`smoke/viewer.html`, and the exact command in `smoke/COMMAND.txt`.

## What it does, per company

1. **Identity anchor** — BRREG Enhetsregisteret (bulk snapshot and live API): legal name, form, address, industry,
   founding/registration dates, employees, stated activity, bankruptcy/liquidation flags.
2. **Official modules** — Regnskapsregisteret latest accounts (values verbatim, with reporting period; a missing
   field stays `not_available`, never zero), roles, subunits (registered workplaces), group structure.
3. **Website discovery and exact-identity gate** — candidates: the registry homepage, the registry e-mail domain,
   name-derived `.no`/`.com` domains, and up to two further name variants that exist in a shipped Common Crawl `.no`
   domain list (`data/cc_no_domains.txt.gz`, no run-time calls). Published only with on-site proof (own organisation number; or full legal name
   for a registry-declared site; or legal name *with form suffix* plus registered address/phone for a guess).
   A site printing another organisation number, parked pages and manager/portal tenant pages are rejected → `ambiguous`.
   A match resting on the name alone is also rejected when the register has a namesake (or a longer register name
   containing ours is shown on the site, unless that entity is in the company's own registered group), when the
   site's privacy statement names another legal entity as controller, or when its copyright holder is a Nordic
   sister company with the same name (AB/ApS/Oy). Further rejections: the site shows the address of a longer register
   namesake; the page is mostly Chinese/Japanese/Korean (a re-used domain); the final site is on a foreign country
   domain; the name appears only inside a web or e-mail address; the register lists the domain as another entity's
   homepage; a one-word name on an unrelated e-mail-domain site lacks its legal form. An organisation number that only
   appears in a list of several companies on a site named after none of our name words is a group listing, not proof.
   On a site that proves us through such a list, posts, profiles and careers jobs are not attributed to us.
   A guessed domain that redirects to another domain is accepted only if the target itself proves the entity (own
   organisation number, or legal name with suffix plus registered address/phone).
   A registry-declared domain that cannot be reached is tried once more as apex ↔ `www.` (same registered domain, same
   proof rules), and a server that omits its intermediate certificate is completed from the certificate's own CA
   Issuers (AIA) URL; certificates are always verified.
4. **Bounded crawl of the verified site** — company-owned social profiles (links on the site whose handle matches the
   company, the site's own Organization `sameAs`, or its one sitewide numeric Facebook page; never fetched),
   careers-page postings (careers pages found by whole path segments or anchor text; one claim per open position with
   an apply cue and no passed deadline; NAV ads linked from the site are confirmed in NAV's feed), dated news/posts
   (RSS/Atom, WordPress REST, news sections found from the site's navigation, listing and article dates; posts of a
   group brand site are not attributed to a subsidiary; a site verified on a sub-path keeps only posts under it;
   theme demo posts in lorem/Latin or other filler text are dropped; a dated permalink beats a listing-card date; job-ad
   pages are not posts), public brand. Careers ads that name another employer ("Arbeidsgiver: ... AS"), positions
   abroad (by jobLocation or by a country/city in the title or URL) and ads whose deadline has passed are not published.
5. **Hiring** — NAV arbeidsplassen's official job feed (public token fetched at run time); an ad is published only
   when its `employer.orgnr` is the company or one of its registered subunits.
6. **Dated official activity** — BRREG's update log (accounts filed, address/board/industry changes, bankruptcy
   or liquidation) and EU TED contract awards won under the organisation number. Official sources keyed by
   organisation number (on by default: `smilefjes`, `state_aid`, `fin_licences`; optional: `rcn_projects`, a 55 MB
   download): Mattilsynet food-safety inspection ratings of the company's serving places, registered public support
   awards (Støtteregisteret), Finanstilsynet licences, and Research Council of Norway funded projects.
7. **Envelope** — sections with availability (`available`, `not_available`, `blocked`, `not_applicable`,
   `ambiguous`, `failed`), claims ↔ evidence, attempts log (candidates and rejection reasons), summary, changes.

Source rights, crawl etiquette and the outbound URL policy: `SOURCES.md`. Known gaps: `LIMITATIONS.md`.

## Envelope shape (abridged)

```json
{"organisation_number": "986389180", "run_id": "...", "state": "completed",
 "run": {"run_id": "...", "started_at": "...", "completed_at": "...", "terminal_status": "completed"},
 "identity": {"name": "HELGEVOLD AS", "legal_form": "AS", "business_address": {...}, "...": "..."},
 "sections": {"legal_identity": {"availability": "available", "claims": 12}, "website_profiles": {...}, "...": {}},
 "claims": [{"id": "c-40", "section": "website_profiles", "field": "official_website", "family": "website",
             "value": "https://helgevold.com/", "availability": "available", "confidence": 0.99,
             "evidence_ids": ["ev-31", "ev-32"], "identity_method": "orgnr_on_site"}],
 "evidence": [{"id": "ev-31", "source_url": "https://helgevold.com/kontakt", "source_class": "company_owned_website",
               "retrieved_at": "...", "content_sha256": "...", "claim_span": "... Org.nr 986 389 180 ...",
               "reporting_period": null, "extraction_method": "website_identity:orgnr_on_site"}],
 "summary": {"text": "HELGEVOLD AS is a private limited company (AS) ... [c-1,c-5]", "supported_claim_ids": [...], "unknowns": [...]},
 "changes": [], "refresh": {...}, "errors": [], "attempts": {...},
 "operations": {"requests": 7, "runtime_ms": 9970, "third_party_cost_usd": 0}}
```

## Tests

```bash
uv run --with pytest pytest -q
```
