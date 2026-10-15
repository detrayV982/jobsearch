import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import tempfile

import jobsearch as js

NOW = 1_800_000_000.0


def iso(seconds_ago):
    return datetime.fromtimestamp(NOW - seconds_ago, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


JSEARCH = {"data": [
    {"job_id": "a", "job_title": "Software Engineer", "employer_name": "Initech", "job_city": "Seattle",
     "job_state": "WA", "job_country": "US", "job_publisher": "LinkedIn", "job_apply_link": "https://linkedin.example/a",
     "job_posted_at_timestamp": NOW - 5 * 3600},
    {"job_id": "b", "job_title": "Junior Developer", "employer_name": "Hooli", "job_is_remote": True,
     "job_publisher": "Indeed", "job_apply_link": "https://indeed.example/b", "job_posted_at_timestamp": NOW - 600},
    {"job_id": "c", "job_title": "No date given", "employer_name": "Umbrella", "job_apply_link": "https://x.example/c"},
    {"job_title": "No id, skipped"},
]}
ADZUNA = {"results": [
    {"id": 1, "title": "Backend Engineer", "company": {"display_name": "Globex"},
     "location": {"display_name": "Seattle, Washington"}, "redirect_url": "https://adzuna.example/1", "created": iso(30 * 3600)},
    # same job as JSearch "a", should be removed as a duplicate
    {"id": 2, "title": "software engineer", "company": {"display_name": "INITECH"},
     "location": {"display_name": "Seattle, WA, US"}, "redirect_url": "https://adzuna.example/2", "created": iso(5 * 3600)},
]}
ENV = {"RAPIDAPI_KEY": "rk", "ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "ak"}


def fake_getter(fail=(), calls=None):
    def getter(url, timeout=20, headers=None):
        if calls is not None:
            calls.append(url)
        for needle in fail:
            if needle in url:
                raise OSError("boom")
        if "rapidapi.com" in url:
            assert headers and headers.get("X-RapidAPI-Key") == "rk"
            return JSEARCH
        if "adzuna.com" in url:
            return ADZUNA
        raise AssertionError(url)
    return getter


class ParseTests(unittest.TestCase):
    def test_jsearch(self):
        jobs = js.parse_jsearch(JSEARCH)
        self.assertEqual([j.id for j in jobs], ["a", "b", "c"])  # the job without an id is skipped
        self.assertEqual(jobs[0].location, "Seattle, WA, US")
        self.assertEqual(jobs[0].via, "LinkedIn")
        self.assertEqual(jobs[0].posted, NOW - 5 * 3600)
        self.assertEqual(jobs[1].location, "Remote")
        self.assertIsNone(jobs[2].posted)

    def test_adzuna(self):
        jobs = js.parse_adzuna(ADZUNA)
        self.assertEqual(jobs[0].company, "Globex")
        self.assertAlmostEqual(jobs[0].posted, NOW - 30 * 3600, delta=1)

    def test_missing_fields_do_not_crash(self):
        self.assertEqual(js.parse_jsearch({}), [])
        self.assertEqual(js.parse_adzuna({"results": [{"id": 9}]})[0].title, "")

    def test_parse_iso(self):
        self.assertIsNone(js.parse_iso("not a date"))
        self.assertIsNone(js.parse_iso(None))
        self.assertEqual(js.parse_iso("2026-01-01T00:00:00Z"), js.parse_iso("2026-01-01T00:00:00+00:00"))


class SafetyTests(unittest.TestCase):
    def test_only_web_links_are_kept(self):
        self.assertEqual(js.safe_url("https://a.example/x"), "https://a.example/x")
        self.assertEqual(js.safe_url("javascript:alert(1)"), "")
        self.assertEqual(js.safe_url("data:text/html,hi"), "")
        self.assertEqual(js.safe_url("//a.example"), "")
        self.assertEqual(js.safe_url(None), "")

    def test_page_escapes_api_data_and_drops_bad_links(self):
        bad = js.Job("JSearch", "x", '<script>alert(1)</script>', 'A"B', "<b>here</b>", "", None, "<i>via</i>")
        page = js.render_page("tok", {"what": '"><img src=x>'}, [bad], ["<note>"], NOW)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn("<img src=x>", page)
        self.assertNotIn("<b>here</b>", page)
        self.assertNotIn("<note>", page)
        self.assertNotIn("<a href", page)  # a job with no safe link is shown without one


class SortAndAgeTests(unittest.TestCase):
    def test_newest_first_unknown_last(self):
        jobs = js.parse_jsearch(JSEARCH)
        self.assertEqual([j.id for j in js.sort_jobs(jobs)], ["b", "a", "c"])

    def test_age_text(self):
        self.assertEqual(js.age_text(None, NOW), "date not given")
        self.assertEqual(js.age_text(NOW - 30, NOW), "just now")
        self.assertEqual(js.age_text(NOW - 600, NOW), "10 minutes ago")
        self.assertEqual(js.age_text(NOW - 3600, NOW), "1 hour ago")
        self.assertEqual(js.age_text(NOW - 5 * 3600, NOW), "5 hours ago")
        self.assertEqual(js.age_text(NOW - 3 * 86400, NOW), "3 days ago")
        self.assertEqual(js.age_text(NOW + 999, NOW), "just now")  # clock skew never shows a negative age


class SearchAllTests(unittest.TestCase):
    def test_merges_dedupes_and_sorts_across_sources(self):
        jobs, notes = js.search_all({"what": "software engineer", "where": "Seattle"}, ENV, fake_getter())
        self.assertEqual(notes, [])
        self.assertEqual([j.id for j in jobs], ["b", "a", "1", "c"])  # Adzuna's copy of Initech is dropped

    def test_builds_the_right_requests(self):
        calls = []
        js.search_all({"what": "data analyst", "where": "Tacoma, WA", "days": "today", "remote": True}, ENV, fake_getter(calls=calls))
        rapid = next(u for u in calls if "rapidapi" in u)
        self.assertIn("query=data+analyst+in+Tacoma%2C+WA", rapid)
        self.assertIn("date_posted=today", rapid)
        self.assertIn("remote_jobs_only=true", rapid)
        adz = next(u for u in calls if "adzuna" in u)
        self.assertIn("what=data+analyst", adz)
        self.assertIn("where=Tacoma%2C+WA", adz)
        self.assertIn("max_days_old=1", adz)

    def test_bad_date_choice_falls_back(self):
        calls = []
        js.search_all({"what": "x", "days": "forever"}, ENV, fake_getter(calls=calls))
        self.assertIn("date_posted=week", next(u for u in calls if "rapidapi" in u))

    def test_missing_keys_explain_themselves(self):
        jobs, notes = js.search_all({"what": "x"}, {}, fake_getter())
        self.assertEqual(jobs, [])
        self.assertEqual(len(notes), 2)
        self.assertTrue(any("RAPIDAPI_KEY" in n for n in notes))
        jobs, notes = js.search_all({"what": "software"}, {"RAPIDAPI_KEY": "rk"}, fake_getter())
        self.assertTrue(jobs and any("Adzuna is off" in n for n in notes))

    def test_one_failing_source_does_not_hide_the_other(self):
        jobs, notes = js.search_all({"what": "x"}, ENV, fake_getter(fail=("rapidapi",)))
        self.assertEqual({j.source for j in jobs}, {"Adzuna"})
        self.assertTrue(any("JSearch failed" in n for n in notes))

    def test_http_error_note_never_contains_the_key(self):
        def getter(url, timeout=20, headers=None):
            raise urllib.error.HTTPError(url, 403, "forbidden", {}, None)
        _, notes = js.search_all({"what": "x"}, ENV, getter)
        self.assertTrue(any("HTTP 403" in n for n in notes))
        self.assertFalse(any("rk" == n or "ak" in n.split() for n in notes))
        self.assertFalse(any("app_key" in n or "X-RapidAPI" in n for n in notes))

    def test_empty_search_asks_for_a_title(self):
        jobs, notes = js.search_all({"what": "  "}, ENV, fake_getter())
        self.assertEqual((jobs, notes), ([], ["Enter a job title to search for."]))


class JSearchEndpointTests(unittest.TestCase):
    def test_falls_back_to_v2_when_search_is_404(self):
        calls = []

        def getter(url, timeout=20, headers=None):
            calls.append(url)
            if "/search?" in url:
                raise urllib.error.HTTPError(url, 404, "nf", {}, None)
            return JSEARCH
        jobs, notes = js.search_all({"what": "x", "remote": True}, {"RAPIDAPI_KEY": "rk"}, getter)
        self.assertTrue(jobs)
        self.assertIn("/search-v2?", calls[1])
        self.assertIn("work_from_home=true", calls[1])
        self.assertNotIn("remote_jobs_only", calls[1])

    def test_only_404_triggers_fallback(self):
        calls = []

        def getter(url, timeout=20, headers=None):
            calls.append(url)
            raise urllib.error.HTTPError(url, 403, "no", {}, None)
        _, notes = js.search_all({"what": "x"}, {"RAPIDAPI_KEY": "rk"}, getter)
        self.assertEqual(len(calls), 1)
        self.assertTrue(any("HTTP 403" in n for n in notes))

    def test_api_error_message_is_shown_and_redacted(self):
        import io

        def getter(url, timeout=20, headers=None):
            raise urllib.error.HTTPError(url, 403, "no", {}, io.BytesIO(b'{"message": "You are not subscribed. key rk"}'))
        _, notes = js.search_all({"what": "x"}, {"RAPIDAPI_KEY": "rk"}, getter)
        joined = " ".join(notes)
        self.assertIn("not subscribed", joined)
        self.assertNotIn("key rk", joined)

    def test_wrapped_data_shapes_parse(self):
        self.assertEqual(len(js.parse_jsearch({"data": {"jobs": JSEARCH["data"]}})), 3)
        self.assertEqual(js.parse_jsearch({"data": ["junk", 5]}), [])


class ServerTests(unittest.TestCase):
    def start(self, getter, clock=lambda: NOW):
        server, token = js.make_server(ENV, getter, port=0, clock=clock)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def post(fields, host=None):
            req = urllib.request.Request(base + "/search", data=urllib.parse.urlencode(fields).encode(), method="POST")
            if host:
                req.add_header("Host", host)
            return urllib.request.urlopen(req)
        return base, token, post

    def test_search_shows_results_newest_first_with_safe_links(self):
        base, token, post = self.start(fake_getter())
        self.assertIn(token, urllib.request.urlopen(base + "/").read().decode())
        page = post({"token": token, "what": "software engineer", "where": "Seattle", "days": "week"}).read().decode()
        self.assertIn("4 results, newest first", page)
        self.assertLess(page.index("Junior Developer"), page.index("Software Engineer"))
        self.assertLess(page.index("Software Engineer"), page.index("Backend Engineer"))
        self.assertIn('href="https://indeed.example/b" target="_blank" rel="noopener noreferrer"', page)
        self.assertIn("via LinkedIn", page)
        self.assertIn("Posted 10 minutes ago", page)

    def test_requires_token_and_localhost_host(self):
        base, token, post = self.start(fake_getter())
        with self.assertRaises(urllib.error.HTTPError) as bad:
            post({"token": "wrong", "what": "x"})
        self.assertEqual(bad.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as rebinding:
            post({"token": token, "what": "x"}, host="evil.example")
        self.assertEqual(rebinding.exception.code, 403)

    def test_repeat_search_uses_the_cache_then_expires(self):
        calls, now = [], [NOW]
        base, token, post = self.start(fake_getter(calls=calls), clock=lambda: now[0])
        fields = {"token": token, "what": "Software Engineer", "where": "Seattle"}
        post(fields)
        first = len(calls)
        post({**fields, "what": "software engineer"})  # same search, different capitals
        self.assertEqual(len(calls), first)
        now[0] += js.CACHE_SECONDS + 1
        post(fields)
        self.assertGreater(len(calls), first)

    def test_failed_searches_are_not_cached(self):
        calls = []
        base, token, post = self.start(fake_getter(fail=("rapidapi", "adzuna"), calls=calls))
        post({"token": token, "what": "x"})
        post({"token": token, "what": "x"})
        self.assertEqual(len(calls), 4)  # both sources tried again the second time


class SecurityTests(unittest.TestCase):
    KEYS = ("rk", "id", "ak")

    def start(self, getter):
        server, token = js.make_server(ENV, getter, port=0, clock=lambda: NOW)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return f"http://127.0.0.1:{server.server_address[1]}", token

    def test_keys_never_appear_in_any_response(self):
        secret_env = {"RAPIDAPI_KEY": "SECRET-RAPID-123", "ADZUNA_APP_ID": "SECRET-ID-456", "ADZUNA_APP_KEY": "SECRET-AK-789"}

        def leaky(url, timeout=20, headers=None):
            # an error that echoes the full request URL and headers, the worst case
            raise ValueError(f"bad request {url} {headers}")
        server, token = js.make_server(secret_env, leaky, port=0, clock=lambda: NOW)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        base = f"http://127.0.0.1:{server.server_address[1]}"
        home = urllib.request.urlopen(base + "/")
        req = urllib.request.Request(base + "/search", data=urllib.parse.urlencode({"token": token, "what": "x"}).encode(), method="POST")
        result = urllib.request.urlopen(req)
        for resp in (home, result):
            blob = (resp.read().decode() + str(resp.headers))
            for secret in secret_env.values():
                self.assertNotIn(secret, blob)
        self.assertIn("[hidden]", urllib.request.urlopen(urllib.request.Request(
            base + "/search", data=urllib.parse.urlencode({"token": token, "what": "y"}).encode(), method="POST")).read().decode())

    def test_redact_handles_url_encoded_keys(self):
        self.assertEqual(js.redact("a=k%2Fey b=k/ey", {"RAPIDAPI_KEY": "k/ey"}), "a=[hidden] b=[hidden]")

    def test_security_headers_are_sent(self):
        base, _ = self.start(fake_getter())
        headers = urllib.request.urlopen(base + "/").headers
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("script-src", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Referrer-Policy"], "same-origin")

    def test_page_has_no_script_tags(self):
        self.assertNotIn("<script", js.render_page("t").lower())

    def test_cross_site_origin_is_rejected(self):
        base, token = self.start(fake_getter())
        req = urllib.request.Request(base + "/search", data=urllib.parse.urlencode({"token": token, "what": "x"}).encode(), method="POST")
        req.add_header("Origin", "https://evil.example")
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req)
        self.assertEqual(err.exception.code, 403)

    def test_bad_content_length_and_oversize_are_rejected(self):
        base, token = self.start(fake_getter())
        for length, code in (("abc", 400), ("-5", 400), ("999999", 413)):
            req = urllib.request.Request(base + "/search", data=b"x", method="POST")
            req.add_unredirected_header("Content-Length", length)
            with self.assertRaises((urllib.error.HTTPError, OSError)) as err:
                urllib.request.urlopen(req, timeout=3)
            if isinstance(err.exception, urllib.error.HTTPError):
                self.assertEqual(err.exception.code, code)

    def test_other_paths_and_get_search_are_refused(self):
        base, _ = self.start(fake_getter())
        for path in ("/.env", "/jobsearch.py", "/search"):
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + path)

    def test_huge_api_responses_are_refused(self):
        class Big:
            def read(self, n): return b"x" * n
            def __enter__(self): return self
            def __exit__(self, *a): return False
        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: Big()
        try:
            with self.assertRaises(ValueError):
                js.http_get_json("https://x.example")
        finally:
            urllib.request.urlopen = original

    def test_only_a_bounded_number_of_results_render(self):
        many = [js.Job("JSearch", str(i), f"Job {i}", "Co", "X", "https://x.example", NOW - i, "") for i in range(300)]
        self.assertEqual(js.render_page("t", {"what": "x"}, many, [], NOW).count("<li>"), js.MAX_RESULTS)


class RealBrowserTest(unittest.TestCase):
    """Submits the form in a real Chromium, the way a person does. Skipped if no browser is installed."""
    def test_form_submit_works_in_a_browser(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright not installed")
        server, _ = js.make_server(ENV, fake_getter(), port=0, clock=lambda: NOW)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium")
            except Exception:
                self.skipTest("no chromium available")
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            page.fill("input[name=what]", "software engineer")
            page.click("button[type=submit]")
            self.assertIn("4 results", page.content())
            browser.close()


class EnvFileTests(unittest.TestCase):
    def test_env_file_does_not_override_real_env(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("# comment\nA=1\nB='two'\nC=\"three\"\n")
            env = {"A": "real"}
            js.load_env_file(p, env)
            self.assertEqual(env, {"A": "real", "B": "two", "C": "three"})


if __name__ == "__main__":
    unittest.main()
