#!/usr/bin/env python3
"""jobsearch: search for jobs by title and location, newest first.

Runs a small page on your own computer. Each result links to the original posting.
Sources (each needs a free API key in .env):
  - JSearch (RapidAPI): listings from sites like LinkedIn and Indeed
  - Adzuna: a second job search API
Standard library only. Run `python3 jobsearch.py` and use the page that opens.
"""
import argparse
import hmac
import html
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
USER_AGENT = "jobsearch/1.0 (personal job search tool)"
DATE_CHOICES = {"today": 1, "3days": 3, "week": 7, "month": 30}
DATE_LABELS = {"today": "Last 24 hours", "3days": "Last 3 days", "week": "Last week", "month": "Last month"}
CACHE_SECONDS = 600  # repeat searches within 10 minutes reuse results so free API quotas last
CACHE_MAX = 50       # keep memory bounded
MAX_RESULTS = 100    # most results shown on the page
MAX_API_BYTES = 2_000_000  # refuse absurdly large API responses
MAX_FORM_BYTES = 20_000
SECURITY_HEADERS = {
    # The page needs no scripts and no outside resources, so lock everything down.
    "Content-Security-Policy": ("default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                "base-uri 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
SECRET_ENV_NAMES = ("RAPIDAPI_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY")


@dataclass(frozen=True)
class Job:
    source: str            # which API returned it: JSearch or Adzuna
    id: str
    title: str
    company: str
    location: str
    url: str
    posted: Optional[float]  # unix time, or None when the source did not say
    via: str = ""          # the site that listed it, such as LinkedIn or Indeed


# ---------- fetching and parsing ----------

def http_get_json(url, timeout=20, headers=None):
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_API_BYTES + 1)
    if len(raw) > MAX_API_BYTES:
        raise ValueError("response too large")
    return json.loads(raw)


def http_error_detail(err):
    """A short, plain-text reason from the API's error body, so failures are explainable."""
    try:
        body = err.read(300).decode("utf-8", errors="replace")
    except Exception:
        return ""
    try:
        data = json.loads(body)
        body = data.get("message") or data.get("error") or body if isinstance(data, dict) else body
    except ValueError:
        pass
    return " ".join(str(body).split())[:200]


def redact(text, env):
    """Remove any secret value from text before it is shown anywhere. Keys stay on the server."""
    text = str(text)
    for name in SECRET_ENV_NAMES:
        value = env.get(name)
        if value:
            for form in (value, urllib.parse.quote(value), urllib.parse.quote_plus(value)):
                text = text.replace(form, "[hidden]")
    return text


def parse_iso(text):
    """'2026-10-14T09:12:33Z' -> unix time, or None if it cannot be read."""
    if not text or not isinstance(text, str):
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def safe_url(url):
    """Only plain web links are allowed, so API data can never inject a javascript: link."""
    try:
        parts = urllib.parse.urlparse(url or "")
    except ValueError:
        return ""
    return url if parts.scheme in ("http", "https") and parts.netloc else ""


def parse_jsearch(data):
    jobs = []
    listings = data.get("data") or []
    if isinstance(listings, dict):  # tolerate a wrapped list such as {"data": {"jobs": [...]}}
        listings = listings.get("jobs") or listings.get("results") or []
    for j in listings:
        if not isinstance(j, dict):
            continue
        if not j.get("job_id"):
            continue
        place = ", ".join(p for p in (j.get("job_city"), j.get("job_state"), j.get("job_country")) if p)
        if j.get("job_is_remote"):
            place = f"{place} (Remote)" if place else "Remote"
        ts = j.get("job_posted_at_timestamp")
        posted = float(ts) if isinstance(ts, (int, float)) else parse_iso(j.get("job_posted_at_datetime_utc"))
        jobs.append(Job(
            source="JSearch",
            id=str(j["job_id"]),
            title=j.get("job_title") or "",
            company=j.get("employer_name") or "",
            location=place,
            url=safe_url(j.get("job_apply_link") or j.get("job_google_link")),
            posted=posted,
            via=j.get("job_publisher") or "",
        ))
    return jobs


def parse_adzuna(data):
    jobs = []
    for j in data.get("results") or []:
        if not j.get("id"):
            continue
        jobs.append(Job(
            source="Adzuna",
            id=str(j["id"]),
            title=j.get("title") or "",
            company=(j.get("company") or {}).get("display_name") or "",
            location=(j.get("location") or {}).get("display_name") or "",
            url=safe_url(j.get("redirect_url")),
            posted=parse_iso(j.get("created")),
            via="Adzuna",
        ))
    return jobs


# JSearch has more than one version of its search endpoint. We try them in order and
# fall back when one answers 404, so a provider-side change does not break the tool.
JSEARCH_ENDPOINTS = ("search", "search-v2")


def jsearch_url(what, where, days, remote, endpoint="search"):
    query = f"{what} in {where}" if where else what
    params = {
        "query": query,
        "date_posted": days if days in DATE_CHOICES else "week",
    }
    if endpoint == "search":
        params.update({"page": "1", "num_pages": "1", "remote_jobs_only": "true" if remote else "false"})
    else:  # newer version uses different names for the remote filter
        params["work_from_home"] = "true" if remote else "false"
    return f"https://jsearch.p.rapidapi.com/{endpoint}?" + urllib.parse.urlencode(params)


def fetch_jsearch(getter, what, where, days, remote, headers):
    """Try each JSearch endpoint until one is found. Only a 404 moves on to the next."""
    last = None
    for endpoint in JSEARCH_ENDPOINTS:
        try:
            return parse_jsearch(getter(jsearch_url(what, where, days, remote, endpoint), headers=headers))
        except urllib.error.HTTPError as err:
            if err.code != 404:
                raise
            last = err
    raise last


def adzuna_url(what, where, days, app_id, app_key, country="us"):
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 50,
        "what": what,
        "max_days_old": DATE_CHOICES.get(days, 7),
        "sort_by": "date",
    }
    if where:
        params["where"] = where
    return f"https://api.adzuna.com/v1/api/jobs/{country}/search/1?" + urllib.parse.urlencode(params)


# ---------- searching ----------

def dedupe(jobs):
    seen, out = set(), []
    for job in jobs:
        key = (job.title.lower().strip(), job.company.lower().strip(), job.location.lower().strip())
        if key not in seen:
            seen.add(key)
            out.append(job)
    return out


def sort_jobs(jobs):
    """Newest first. Jobs with no posted date go last."""
    return sorted(jobs, key=lambda j: (j.posted is None, -(j.posted or 0)))


def age_text(posted, now):
    if posted is None:
        return "date not given"
    seconds = max(0, now - posted)
    if seconds < 120:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


def search_all(params, env, getter=http_get_json):
    """Return (jobs newest first, notes to show the user). One source failing does not stop the other."""
    what = (params.get("what") or "").strip()[:100]
    where = (params.get("where") or "").strip()[:100]
    days = params.get("days") if params.get("days") in DATE_CHOICES else "week"
    remote = bool(params.get("remote"))
    notes = []
    if not what:
        return [], ["Enter a job title to search for."]

    sources = []
    rapid_key = env.get("RAPIDAPI_KEY")
    if rapid_key:
        headers = {"X-RapidAPI-Key": rapid_key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
        sources.append(("JSearch", lambda: fetch_jsearch(getter, what, where, days, remote, headers)))
    else:
        notes.append("JSearch is off. Add RAPIDAPI_KEY to .env to include LinkedIn and Indeed listings.")

    app_id, app_key = env.get("ADZUNA_APP_ID"), env.get("ADZUNA_APP_KEY")
    if app_id and app_key:
        sources.append(("Adzuna", lambda: parse_adzuna(getter(adzuna_url(what, where, days, app_id, app_key)))))
    else:
        notes.append("Adzuna is off. Add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env to include it.")

    if not sources:
        return [], notes

    jobs = []
    for name, fetch in sources:
        try:
            jobs.extend(fetch())
        except urllib.error.HTTPError as err:
            notes.append(redact(f"{name} failed (HTTP {err.code}). {http_error_detail(err)} "
                                "Check the key in .env and your plan's limits.", env))
        except Exception as exc:  # network or parse problems should not hide the other source
            notes.append(redact(f"{name} failed: {type(exc).__name__}: {exc}", env))
    return sort_jobs(dedupe(jobs)), notes


# ---------- the page ----------

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>jobsearch</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;background:#0e1116;color:#e6e9ef;line-height:1.5}}
h1{{font-size:1.5rem;margin-bottom:4px}} .sub{{color:#9aa4b2;margin-top:0}}
form{{display:grid;gap:12px;grid-template-columns:1fr 1fr;margin:20px 0}}
label{{font-weight:600;font-size:.9rem}} .wide{{grid-column:1/-1}}
input[type=text],select{{width:100%;padding:9px;margin-top:4px;border-radius:8px;border:1px solid #2a313c;background:#161b22;color:inherit;box-sizing:border-box;font-size:1rem}}
button{{padding:10px 18px;border:0;border-radius:8px;background:#5eead4;color:#062a25;font-weight:700;cursor:pointer;font-size:1rem}}
.note{{background:#2b2411;border-radius:8px;padding:8px 12px;margin:8px 0}}
ul{{list-style:none;padding:0}} li{{border:1px solid #2a313c;background:#161b22;border-radius:12px;padding:14px 16px;margin:10px 0}}
li a{{color:#5eead4;font-weight:700;font-size:1.05rem;text-decoration:none}} li a:hover{{text-decoration:underline}}
.meta{{color:#c3cad5}} small{{color:#9aa4b2}}
@media(max-width:560px){{form{{grid-template-columns:1fr}}}}
</style></head><body>
<h1>jobsearch</h1>
<p class="sub">Search by title and location. Newest postings first. Click a title to open the posting.</p>
<form method="post" action="/search">
<input type="hidden" name="token" value="{token}">
<label class="wide">Job title<input type="text" name="what" value="{what}" placeholder="software engineer" required></label>
<label>Location<input type="text" name="where" value="{where}" placeholder="Seattle, WA"></label>
<label>Posted within<select name="days">{day_options}</select></label>
<label class="wide"><input type="checkbox" name="remote" {remote}> Remote jobs only (JSearch)</label>
<div class="wide"><button type="submit">Search</button></div>
</form>
{notes}
{summary}
<ul>{items}</ul>
</body></html>"""


def render_page(token, params=None, jobs=None, notes=None, now=None):
    esc = lambda v: html.escape(str(v), quote=True)
    params = params or {}
    now = now if now is not None else time.time()
    day_options = "".join(
        f'<option value="{k}" {"selected" if k == params.get("days", "week") else ""}>{esc(v)}</option>'
        for k, v in DATE_LABELS.items()
    )
    items = []
    for job in (jobs or [])[:MAX_RESULTS]:
        title = esc(job.title or "Untitled")
        head = (f'<a href="{esc(job.url)}" target="_blank" rel="noopener noreferrer">{title}</a>'
                if job.url else f"<strong>{title}</strong>")
        place = " &middot; ".join(esc(p) for p in (job.company, job.location) if p)
        via = f" &middot; via {esc(job.via)}" if job.via else ""
        items.append(f'<li>{head}<div class="meta">{place}</div>'
                     f"<small>Posted {esc(age_text(job.posted, now))}{via}</small></li>")
    summary = ""
    if jobs is not None and params.get("what"):
        shown = min(len(jobs), MAX_RESULTS)
        summary = f"<p>{shown} result{'s' if shown != 1 else ''}, newest first.</p>"
    return PAGE.format(
        token=esc(token),
        what=esc(params.get("what", "")),
        where=esc(params.get("where", "")),
        remote="checked" if params.get("remote") else "",
        day_options=day_options,
        notes="".join(f'<div class="note">{esc(n)}</div>' for n in notes or []),
        summary=summary,
        items="".join(items),
    )


def make_server(env, getter=http_get_json, port=8765, clock=time.time):
    """Build (not start) a search server bound to this computer only. Returns (server, token)."""
    token = secrets.token_urlsafe(16)
    holder = {}
    cache = {}  # search -> (when, jobs, notes)

    class Handler(BaseHTTPRequestHandler):
        timeout = 10  # drop slow or stalled connections instead of hanging

        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            # Blocks DNS-rebinding tricks: only accept requests addressed to localhost.
            return self.headers.get("Host", "") in (
                f"127.0.0.1:{holder['port']}", f"localhost:{holder['port']}")

        def _origin_ok(self):
            # Browsers send Origin on form posts. If present it must be this page.
            origin = self.headers.get("Origin")
            return origin is None or origin in (
                f"http://127.0.0.1:{holder['port']}", f"http://localhost:{holder['port']}")

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "forbidden", "text/plain")
            if self.path != "/":
                return self._send(404, "not found", "text/plain")
            self._send(200, render_page(token))

        def do_POST(self):
            if not self._host_ok() or not self._origin_ok() or self.path != "/search":
                return self._send(403, "forbidden", "text/plain")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send(400, "bad request", "text/plain")
            if length < 0:
                return self._send(400, "bad request", "text/plain")
            if length > MAX_FORM_BYTES:
                return self._send(413, "too large", "text/plain")
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            form = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
            if not hmac.compare_digest(form.get("token", ""), token):
                return self._send(403, "bad token: reload the page", "text/plain")
            params = {
                "what": (form.get("what") or "").strip()[:100],
                "where": (form.get("where") or "").strip()[:100],
                "days": form.get("days") if form.get("days") in DATE_CHOICES else "week",
                "remote": form.get("remote") == "on",
            }
            key = (params["what"].lower(), params["where"].lower(), params["days"], params["remote"])
            hit = cache.get(key)
            if hit and clock() - hit[0] < CACHE_SECONDS:
                jobs, notes = hit[1], hit[2]
            else:
                jobs, notes = search_all(params, env, getter)
                if jobs:
                    if len(cache) >= CACHE_MAX:
                        cache.pop(next(iter(cache)))  # drop the oldest entry
                    cache[key] = (clock(), jobs, notes)
            self._send(200, render_page(token, params, jobs, notes, clock()))

    server = HTTPServer(("127.0.0.1", port), Handler)
    holder["port"] = server.server_address[1]
    return server, token


# ---------- running ----------

def load_env_file(path, env):
    """Read KEY=VALUE lines from a .env file without overriding real environment variables."""
    try:
        lines = Path(path).read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def warn_if_env_is_shared(path):
    """On Mac and Linux, warn when other users on this computer could read the key file."""
    try:
        mode = Path(path).stat().st_mode
    except OSError:
        return
    if os.name == "posix" and mode & 0o077:
        print(f"Warning: {path.name} can be read by other users. Fix it with: chmod 600 {path.name}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not open the page automatically")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    load_env_file(HERE / ".env", env)
    warn_if_env_is_shared(HERE / ".env")
    server, _ = make_server(env, port=args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"jobsearch is running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
