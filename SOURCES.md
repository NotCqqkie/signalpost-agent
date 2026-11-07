# Source rights, secrets and outbound URL policy

The agent uses **no credentials of any kind**: no API keys, no accounts, no paid providers, no LLM.
Third-party cost per official run: **$0**.

## Sources, in priority order

| Source | Used for | Access and rights |
|---|---|---|
| Brønnøysundregistrene Enhetsregisteret — bulk CSV (optional `--bulk`) and `data.brreg.no/enhetsregisteret/api/enheter/{org}` | identity anchor: legal name, form, address, industry, founding/registration dates, employees, declared homepage and e-mail domain (as website *candidates*), bankruptcy/liquidation flags | open data, NLOD 2.0; public API, no key |
| Enhetsregisteret `/enheter/{org}/roller`, `/underenheter?overordnetEnhet={org}`, `/konsernstruktur/{org}` | leadership roles, registered workplaces (subunits; their declared homepage/e-mail are website candidates), group links | open data, NLOD 2.0 |
| Regnskapsregisteret `data.brreg.no/regnskapsregisteret/regnskap/{org}` | latest filed annual accounts (values copied verbatim with reporting period; absent fields stay absent) | open data, NLOD 2.0 |
| NAV arbeidsplassen job-vacancy feed `pam-stilling-feed.nav.no` | active job postings whose `employer.orgnr` is the company or one of its registered subunits | free public API; NAV publishes a rotating **public** token at `/api/publicToken` for everyone ("Alle kan bruke tenesta", https://arbeidsplassen.nav.no/vilkar-api). Only ACTIVE ads are published, each linked to its NAV ad page; contact persons are never copied |
| Enhetsregisteret update log `data.brreg.no/enhetsregisteret/api/oppdateringer/enheter?organisasjonsnummer={org}&includeChanges=true` | dated official register events (accounts filed, address/name/industry/capital changes, board/CEO registration changes via roles `sistEndret`, bankruptcy/liquidation); undetailed system-wide updates are not published; phone/e-mail values are never published | open data, NLOD 2.0; documented API, own throttle 10 req/s |
| EU TED Search API `api.ted.europa.eu` | public-procurement contract awards whose winner identifier equals the organisation number and winner country is NOR | anonymous public API; reuse authorised with source acknowledgement (ted.europa.eu legal notice, Decision 2011/833/EU), carried on every claim; notice pages are linked, never fetched |
| Mattilsynet smilefjes export `smilefjes.mattilsynet.no/api/tilsyn.csv` (strategy `smilefjes`) | official food-safety inspection ratings (`ratings_reviews`) and dated inspections (`dated_activity`) of serving places whose `orgnummer` is the company or one of its registered subunits | dataset "Smilefjestilsyn på serveringssteder", CC BY 4.0, attribution "Kilde: Mattilsynet" on every claim; robots.txt 404; one download per run. Fallback if the export fails: the site's search index + establishment pages, published only when the page's "Orgnr." matches. The dataset host `data.mattilsynet.no` (robots `Disallow: /`) is not used |
| Brønnøysund Støtteregisteret `stottetiltak-registerinfo-api.app.brreg.no` (strategy `state_aid`) | registered public support awards (grants, loans, tax/duty relief) whose recipient (`stoettemottaker`) is the organisation number; one claim per support scheme and giver (latest award date, instrument, amount as registered, award count); scheme names from the scheme search | open data, NLOD 2.0 (API distribution catalogued on data.norge.no); no key; one POST per company + one per distinct scheme, ≤ 4 req/s |
| Finanstilsynet virksomhetsregister `api.finanstilsynet.no/registry/v2` (strategy `fin_licences`) | licences held by the entity with exactly this organisation number (type and `registeredDate`, e.g. accounting firm, estate agency, insurance agency, bank) | Finanstilsynet's open API for the register, no key; robots.txt 404; 1–2 GETs per company, ≤ 4 req/s |
| Forskningsrådet open data `soknader2` on `raw.githubusercontent.com/Forskningsradet/open-data` (strategy `rcn_projects`) | Research Council of Norway projects granted to the organisation number (phase granted/completed, amount > 0), dated by the application date | NLOD 2.0; one download per run; prosjektbanken pages are linked, never fetched (its robots.txt disallows AI crawlers) |
| Wikidata Query Service (P2333 organisation number → P856 official website) | website *candidates* only | CC0; one SPARQL request per 200 companies with an identifying User-Agent |
| Common Crawl web graph, domain vertices `cc-main-2026-aug-sep-oct` (strategy `cc_domains`) | website *candidates* only: name variants of the legal name (leading-word prefixes, weak words dropped, initials merged, ø/å/æ as oe/aa/ae) that exist as listed .no domains, tried after the fixed name guesses with the unchanged identity gate | Common Crawl terms of use (commoncrawl.org/terms-of-use): limited licence for lawful use; one bulk download at build time (`scripts/build_cc_domain_list.py`), shipped as `data/cc_no_domains.txt.gz` (280,800 labels); **no call to Common Crawl at run time** |
| The company's own website, once verified | public brand, self-description, company-owned social profiles (outbound links on the site), careers page postings, dated news/posts (RSS/Atom, WordPress REST `wp/v2/posts`, article pages with `datePublished`) | company-owned public pages; robots.txt obeyed |

Not used: search engines or search-result pages, LinkedIn, Meta/Facebook/Instagram, X, TikTok, YouTube, Glassdoor,
Indeed, Google, Proff/Purehelp/1881/Gulesider and other directories, BRREG `w2.brreg.no/kunngjoring/` (disallowed by
robots.txt), Norid RDAP/whois (terms forbid any commercial use and storing significant parts; anonymous limit 10 lookups/min and 300/day per IP — cannot serve a 1,100-company run), Peppol Directory search API (`/search/` disallowed by robots.txt), `data.mattilsynet.no` (robots.txt `Disallow: /`), DiBK sentral godkjenning API (`/api/` disallowed by robots.txt), Arbeidstilsynet's staffing-agency API (access-controlled). Social profile URLs are **published as links found on the
verified company site; the platforms themselves are never fetched.**

## Publication gates (exact identity)

- A website is published only with on-site proof: its own organisation number (mod-11 checked) on a fetched page;
  or, for a registry/subunit/Wikidata-declared homepage or registry e-mail domain, the full legal name in the site's
  identity text; or, for a name-derived guess, the full legal name **with its legal-form suffix** plus the registered
  address or registry phone. A site that prints a *different* organisation number as owner is never published.
  Parked/placeholder pages and redirects of a guessed domain to another domain are rejected.
- Everything else is `ambiguous` (candidates and reasons are kept in `attempts`) or `not_available`.
- Jobs: NAV ads only when the feed entry's `employer.orgnr` equals the company or one of its subunits.
- Profiles/news/careers: only from the verified site.
- Financial values are copied from the register; a missing field is `not_available`, never zero.

## Crawl etiquette and outbound URL safety

- Honest User-Agent `SignalpostAgent/1.0 (Norwegian company research; respects robots.txt; low rate)`.
- robots.txt fetched once per host and obeyed (RFC 9309: 4xx = no rules, 5xx/unreachable = disallow).
- At most one request start per host per second (0.5 s for the NAV API); one polite retry on 429/503.
- Bounded crawl per company: homepage + up to 3 identity pages, 1 careers page, 1 feed or WordPress posts call or
  up to 4 article pages, up to 3 sitemap files. Per-company time cap and a global deadline.
- SSRF guard on every URL and every redirect hop: only public `http(s)` hosts; loopback, private, link-local,
  multicast and reserved addresses are refused; byte caps per response.

## Secrets

None are required. The run reads no environment secrets. Optional environment variables are non-secret tuning
knobs: `SIGNALPOST_TIME_BUDGET_SECONDS`, `SIGNALPOST_WORKERS`, `SIGNALPOST_BULK`.

## Declared caches

Static data shipped in the repository (no secrets, no per-run network): `data/shared_domains.txt.gz` (1,608 e-mail/homepage domains that the BRREG Enhetsregisteret lists for at least 8 entities, e.g. housing-co-op managers and pension administrators; built by `scripts/build_shared_domains.py` from the NLOD bulk file of 2026-11-05; a name-only website match on such a domain is not accepted), `data/cc_no_domains.txt.gz` (Common Crawl
domain list, see above) and `data/common_name_heads.txt.gz` (2,115 first name-words shared by >= 20 companies of the
public Signalpost universe archive; such words are never used alone as a domain candidate). Rebuild with
`scripts/build_cc_domain_list.py`. Otherwise every run fetches its sources live (the optional `--bulk` file is the frozen registry snapshot that Builderr
supplies). The NAV feed index is rebuilt in memory at the start of every run.
