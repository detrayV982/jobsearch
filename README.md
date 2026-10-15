# jobsearch

A search page that runs on your own computer. Type a job title and a location, and it lists matching postings with the newest first. Click a title to open the original posting.

## Setup (Mac)

1. Check Python: `python3 --version` (3.9 or newer). Nothing else to install.
2. Copy `.env.example` to `.env`.
3. Get a JSearch key (this is what brings in LinkedIn and Indeed listings): make a free account at rapidapi.com, search "JSearch", subscribe to the free plan, and copy your key into `.env` as `RAPIDAPI_KEY`.
4. Optional second source: a free key at developer.adzuna.com. Put the app id and key in `.env`.
   Each source is skipped with a note on the page if its key is missing.

## Run

```
python3 jobsearch.py
```

Your browser opens at http://127.0.0.1:8765. Enter a job title and location, pick how recent, and click Search. Use `--port 9000` to change the port or `--no-browser` to skip opening it.

LinkedIn and Indeed have no public search API, and scraping them breaks their terms. JSearch is a licensed aggregator that returns their listings, and each result shows which site it came from.

## Tests

```
python3 -m unittest
```
