# jobsearch

A personal on-demand job search page. Built for TCSS 490 Artifact 2.

## How it works

- `jobsearch.py` is the whole tool. Python standard library only.
- Sources: JSearch API (RapidAPI) and Adzuna API. Do not scrape LinkedIn or Indeed.
- Results are merged, de-duplicated by title, company and location, and sorted newest first. Jobs with no posted date go last.
- The page binds to 127.0.0.1 only, checks the Host header, and requires a per-run token on every search (POST).
- All API text is HTML-escaped and only http/https links are kept. Links open in a new tab with rel="noopener noreferrer".
- Successful searches are cached for 10 minutes to protect the free API quotas. Failed searches are not cached.
- `.env` holds API keys and is never committed. Keys stay on the server: they are sent only to the API hosts, never put in the page, and `redact()` strips them from any error text shown to the user.
- Every response carries a strict Content-Security-Policy (no scripts, no outside resources), nosniff, frame denial and Referrer-Policy same-origin (no-referrer makes browsers send Origin: null on form posts, which the Origin check rejects). POSTs must pass the token, Host and Origin checks. Form size, API response size, result count and cache size are all capped.

## Commands

- Run: `python3 jobsearch.py`
- Tests: `python3 -m unittest`

## Rules for working in this repo

- Never put an API key in code or LOG.md, and never put a key in an error message.
- Keep it to one file and the standard library unless there is a clear reason.
- Add a test for every behavior change and run the tests before committing.
- Keep LOG.md current: what I asked for, what changed, and which of my answers changed the result.
