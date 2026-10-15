# Build log: Artifact 2, jobsearch

Tool: Claude (web chat), then Claude Code once I have it set up. Started Thursday, October 15, 2026.

## Feedback I am building on

Chris's feedback on Artifact 1: "Next time give more direction up front, as you planned, and record in LOG.md which of your answers changed the result."

This log has a table of the direction I gave and what each answer changed, including where Claude pushed back or made a call I did not ask for.

## Direction I gave and what it changed

| What I said | What it changed in the result |
| --- | --- |
| Idea: a job tool that scans for new postings and tells me first. Sources: both career pages and job boards. Alert by Discord. Python. Runs on my laptop only. | Claude built "jobwatch": a checker with a Discord alert, a loop mode, and a settings page. It covered Greenhouse, Lever, Adzuna and JSearch. 27 tests passed on my Mac, and a live Greenhouse check worked. |
| Use job search API keys like LinkedIn, and let me set criteria and location on a website. | LinkedIn has no public job search API for individuals. Claude added JSearch (RapidAPI), which returns listings from LinkedIn and Indeed, plus a local settings page. |
| "Remake this. I don't want it running constantly. I want to search a job and a location, have it pull results sorted by when posted, and click one to go to the posting. Can it use Indeed or LinkedIn?" | This replaced the whole design. The watcher, Discord alerts, seen-job tracking, company boards and settings file are gone. In their place is a search page: title and location in, newest postings first out, each title links to the original posting. For Indeed and LinkedIn Claude said not directly, and used JSearch and Adzuna instead. Results show "via LinkedIn", "via Indeed" and so on. |

The change from an always-running alert tool to an on-demand search was my decision, and it changed the architecture the most. The old version is not in this repo.

## Decisions Claude made without asking me

- Searches are sent with POST and a per-run token, and the page only listens on my own computer, so no other site or machine can trigger searches that use my API keys.
- Results are cached for 10 minutes, because the free API plans have small monthly limits.
- Duplicates across sources are removed by title, company and location. The first copy is kept, which is the JSearch one.
- Jobs with no posted date are shown last instead of hidden.
- Only http and https links are used, so a bad link from an API cannot run script.
- A "posted within" filter (today, 3 days, week, month) and a remote-only checkbox were added without my asking.
- If one source fails or has no key, the other still runs and the page says what happened.
- Security pass, after I asked for keys to be kept out of the front end and for decent security: keys only exist on the server and are never sent to the browser. Error text is scrubbed of key values (a test feeds in an error that echoes the full URL and headers). Added strict security headers with no scripts allowed, an Origin check on searches, size limits on the form and on API responses, a cap on results and cache size, a 10 second connection timeout, and a warning if `.env` is readable by other users. Nine tests cover this.
- A bug from that security pass: my first version told browsers to send no referrer, which makes Chrome send `Origin: null` on the search form, so my own Origin check blocked every search with "forbidden". I found it when my Terminal screenshot showed the page saying forbidden. Claude reproduced it in a real browser, switched to `Referrer-Policy: same-origin`, and added a test that submits the form in real Chromium. My tests had missed it because they did not send an Origin header the way a browser does.

## What has been checked

- 34 unit tests pass in Claude's workspace: parsing of both APIs, sorting and age text, merging and de-duplication, request building, missing keys, one source failing, error messages never containing a key, the page escaping hostile text, token, Host and Origin checks, security headers, key redaction, size limits, caching and cache expiry.
- The page was opened in a real browser with fake results and the layout looked right. The results in that screenshot were test data, not real jobs.
- The earlier jobwatch version ran its 27 tests on my Mac, and a live Greenhouse request worked there, so my laptop can reach job sites.

## What has not been checked

- Adzuna has not been tried live (no Adzuna keys). Claude's own workspace cannot reach either API, so every live check was done on my laptop.
- Whether the first result for a title like "software engineer" is actually relevant. That depends on the APIs.

## First real run

My first live search returned "JSearch failed (HTTP 404)". That proved my Mac can reach RapidAPI and that the endpoint path Claude used from documentation was wrong or out of date. Claude could not test live from its own workspace, so this was only found on my laptop. Fix: the tool now tries JSearch's `search` endpoint and falls back to `search-v2` on a 404, using that version's parameter names, and error notes now show the API's own message (with keys removed). Four tests cover this. After that change, I ran a real search on my laptop and it worked (Oct 15, 12:43 PM, reported by me, not screenshotted).

## Parts I am trusting

- The JSearch and Adzuna response formats, until I see a real search work.
- The de-duplication rule is simple and may hide two real jobs that share a title, company and location, or miss duplicates with different wording.
- I have read the test names but not every line of `jobsearch.py`.

## To do

1. Write down here what I searched and whether the results looked right.
2. Create the project repo and push the code and this log (not `.env`).
3. Fill in the Artifact 2 card on my portfolio (title, repo link, reflection) and submit the same portfolio link on Canvas.
