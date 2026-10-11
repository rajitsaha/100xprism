"""Real loopback requests exercise dashboard authorization and snapshot serving."""
import http.client
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))


class DashboardAggregationTests(unittest.TestCase):
    def test_durable_build_never_scans_sources_or_uses_network_or_legacy_cache(self):
        spec = importlib.util.spec_from_file_location("economics_dashboard", os.path.join(os.path.dirname(__file__), "token-dashboard.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module._value, "cached_scan", side_effect=AssertionError("source discovery")), \
             patch.object(module, "usage_collectors", side_effect=AssertionError("source scan")), \
             patch.object(module._value, "load_store", side_effect=AssertionError("legacy read")), \
             patch.object(module._value, "save_store", side_effect=AssertionError("legacy write")), \
             patch.object(module, "_github_insights", side_effect=AssertionError("network")):
            result = module.build(verbose=False, summaries=[], value_context=({}, {}), value_store={}, offline=True)
        self.assertEqual(result["totals"]["input"], 0)
        self.assertFalse(result["github"]["enabled"])

    def test_model_cache_counterfactual(self):
        spec = importlib.util.spec_from_file_location("economics_dashboard", os.path.join(os.path.dirname(__file__), "token-dashboard.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        metrics = module.cache_metrics({"claude-sonnet": {"input": 100, "output": 50, "cache_read": 900, "cache_write": 100}})
        self.assertAlmostEqual(metrics["cache_hit_ratio"], 0.9)
        self.assertAlmostEqual(metrics["estimated_savings"], 900 * (3 - .3) / 1_000_000)
        tokens = {"input": 100, "output": 50, "cache_read": 900, "cache_write": 100}
        result = module.build(verbose=False, summaries=[{
            "session_id": "fixture", "project": "repository", "tool": "claude-code",
            "totals": tokens, "by_model": {"claude-sonnet": tokens}, "turns": 1,
        }], value_context=({}, {}), value_store={}, offline=True)
        self.assertEqual(result["cache"], result["cache_by_model"]["claude-sonnet"])
        self.assertEqual(result["cache"], result["by_session"][0]["cache"])

    def test_cursor_prose_search_and_claude_project_metadata(self):
        from adapters import claude_code
        from adapters._incremental import extract_content
        with tempfile.TemporaryDirectory() as tmp:
            cursor = os.path.join(tmp, "cursor.jsonl")
            with open(cursor, "w") as stream:
                stream.write(json.dumps({"role": "user", "message": {"content": [{"type": "text", "text": "hello cursor"}, {"type": "tool_result", "content": "exclude"}]}}) + "\n")
            content = extract_content("cursor", cursor)
            self.assertEqual(content["rows"][0]["text"], "hello cursor")
            claude = os.path.join(tmp, "claude.jsonl")
            with open(claude, "w") as stream:
                stream.write(json.dumps({"type": "user", "cwd": "/fixture/repository", "sessionId": "fixture", "message": {"role": "user", "content": "prompt"}}) + "\n")
            self.assertEqual(claude_code.parse_incremental(claude)["summary"]["cwd"], "/fixture/repository")

    def test_first_print_collection_does_not_write_runtime_bytecode(self):
        spec = importlib.util.spec_from_file_location("print_dashboard", os.path.join(os.path.dirname(__file__), "token-dashboard.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            scripts = os.path.join(tmp, "scripts")
            os.mkdir(scripts)
            with open(os.path.join(scripts, "probe.py"), "w") as stream:
                stream.write("value = 42\n")
            with open(os.path.join(scripts, "token-collector.py"), "w") as stream:
                stream.write("import probe\n")
            module.__file__ = os.path.join(scripts, "token-dashboard.py")
            class PrintStore(FakeStore):
                reads = 0
                def latest_snapshot(self):
                    PrintStore.reads += 1
                    return None if PrintStore.reads == 1 else {"totals": {"input": 42}}
            # Run an actual child import; only the store reader is replaced.
            with patch("_token_store.TokenStore", lambda path: PrintStore()), patch.object(module, "print_summary"):
                module._sqlite_dashboard(SimpleNamespace(root=tmp, db=os.path.join(tmp, "tokens.sqlite3"), text=True, oneline=False))
            self.assertFalse(os.path.exists(os.path.join(scripts, "__pycache__")))


class FakeStore:
    def latest_snapshot(self):
        return {"totals": {"input": 42}, "generated": "saved"}

    def status(self):
        return {"locations": [], "content_search_enabled": True}

    def search(self, query, **filters):
        return [{"segments": [{"text": "<script>alert(1)</script>", "matched": True}]}]

    def close(self):
        pass


class TokenHTTPTests(unittest.TestCase):
    def setUp(self):
        import _token_http
        self.http = _token_http
        self.tmp = tempfile.TemporaryDirectory()
        self.collects = []
        self.server = _token_http.DashboardServer(("127.0.0.1", 0), "<html><script>load()</script></html>",
                                                 lambda: FakeStore(), lambda: self.collects.append(1),
                                                 runtime_path=os.path.join(self.tmp.name, "runtime.json"), idle_seconds=600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        con = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        con.request(method, path, body=body, headers=headers or {})
        result = con.getresponse()
        status, response_headers, data = result.status, dict(result.getheaders()), result.read()
        con.close()
        return status, response_headers, data

    def login(self):
        status, headers, body = self.request("POST", "/api/session", json.dumps({"token": self.server.token}),
                                            {"Origin": self.origin, "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";", 1)[0], json.loads(body)["csrf"]

    def test_saved_snapshot_and_host_validation(self):
        status, _, data = self.request("GET", "/api/data")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["totals"]["input"], 42)
        self.assertEqual(self.collects, [])
        for host in ("evil.example", "localhost", "127.0.0.1:1"):
            self.assertEqual(self.request("GET", "/api/data", headers={"Host": host})[0], 403)

    def test_search_requires_cookie_and_stays_structured(self):
        self.assertEqual(self.request("GET", "/api/search?q=hello")[0], 401)
        cookie, _ = self.login()
        status, _, body = self.request("GET", "/api/search?q=hello", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["results"][0]["segments"][0]["text"], "<script>alert(1)</script>")

    def test_rejects_malformed_and_unknown_requests(self):
        post = {"Origin": self.origin, "Content-Type": "application/json"}
        self.assertEqual(self.request("GET", "/api/status")[0], 200)
        self.assertEqual(self.request("GET", "/nope")[0], 404)
        self.assertEqual(self.request("GET", "/api/session")[0], 405)
        cookie, csrf = self.login()
        self.assertEqual(self.request("GET", "/api/search?q=" + "a" * 5000, headers={"Cookie": cookie})[0], 400)
        self.assertEqual(self.request("GET", "/api/search?q=x&limit=bad", headers={"Cookie": cookie})[0], 400)
        self.assertEqual(self.request("POST", "/api/session", "{}", {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.request("POST", "/api/session", "x" * 5000, post)[0], 400)
        self.assertEqual(self.request("POST", "/api/session", "[]", post)[0], 400)
        self.assertEqual(self.request("POST", "/api/session", "not json", post)[0], 400)
        self.assertEqual(self.request("POST", "/api/session", json.dumps({"token": "wrong"}), post)[0], 401)
        self.assertEqual(self.request("POST", "/api/nope", "{}", {**post, "Cookie": cookie})[0], 404)
        authed = {**post, "Cookie": cookie, "X-Prism-CSRF": csrf}
        self.assertEqual(self.request("POST", "/api/refresh", "{}", {**post, "Cookie": cookie, "X-Prism-CSRF": "bad"})[0], 403)
        self.assertEqual(self.request("POST", "/api/github", "{}", authed)[0], 503)
        status, _, body = self.request("POST", "/api/refresh", "{}", authed)
        self.assertEqual((status, self.collects), (200, [1]))
        self.assertEqual(json.loads(body)["totals"]["input"], 42)

    def test_store_failure_is_reported_without_details(self):
        self.server.store_factory = lambda: (_ for _ in ()).throw(RuntimeError("secret path"))
        status, _, body = self.request("GET", "/api/data")
        self.assertEqual(status, 503)
        self.assertNotIn(b"secret", body)

    def test_duplicate_tabs_reload_and_mutation_origin_csrf(self):
        cookie, csrf = self.login()
        self.assertEqual(self.login()[0], cookie)
        for headers in ({}, {"Cookie": cookie}, {"Cookie": cookie, "Origin": "http://evil.example", "X-Prism-CSRF": csrf}):
            self.assertNotEqual(self.request("POST", "/api/refresh", "{}", headers)[0], 200)
        self.assertEqual(self.request("GET", "/api/refresh")[0], 405)
        status, _, body = self.request("POST", "/api/session", "{}", {"Cookie": cookie, "Origin": self.origin})
        self.assertEqual(status, 200)
        self.assertNotEqual(json.loads(body)["csrf"], csrf)
        self.assertEqual(self.request("POST", "/api/refresh", "{}", {"Cookie": cookie, "Origin": self.origin, "X-Prism-CSRF": csrf})[0], 200)
        self.assertEqual(len(self.collects), 1)

    def test_csp_nonce_and_runtime_permissions(self):
        status, headers, data = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("nonce-", headers["Content-Security-Policy"])
        self.assertIn("nonce=", data.decode())
        self.assertIn("object-src 'none'", headers["Content-Security-Policy"])
        runtime = self.server.runtime_path
        self.assertEqual(os.stat(runtime).st_mode & 0o777, 0o600)
        with open(runtime) as stream:
            self.assertEqual(json.load(stream)["token"], self.server.token)

    def test_idle_threshold(self):
        self.assertFalse(self.server.idle_expired(self.server.last_activity + 599))
        self.assertTrue(self.server.idle_expired(self.server.last_activity + 601))

    def test_github_is_an_authenticated_interactive_action(self):
        calls = []
        self.server.github = lambda: calls.append(1) or {"enabled": True, "fetched": True}
        self.request("GET", "/api/data")
        self.assertEqual(calls, [])
        self.assertEqual(self.request("POST", "/api/github", "{}", {"Origin": self.origin})[0], 401)
        cookie, csrf = self.login()
        status, _, data = self.request("POST", "/api/github", "{}", {"Origin": self.origin, "Cookie": cookie, "X-Prism-CSRF": csrf})
        self.assertEqual(status, 200)
        self.assertEqual(calls, [1])
        self.assertTrue(json.loads(data)["github"]["fetched"])


if __name__ == "__main__":
    unittest.main()
