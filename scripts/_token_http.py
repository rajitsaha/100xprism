"""Authenticated, idle-bounded loopback serving of committed economics snapshots."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def _nonce_page(page, nonce):
    return page.replace("<script>", f'<script nonce="{nonce}">')


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, page, store_factory, collect, runtime_path,
                 idle_seconds=600, register=None, github=None):
        self.page, self.store_factory, self.collect = page, store_factory, collect
        self.github = github
        self.interactive_github = None
        self.token = secrets.token_hex(32)
        self.session_cookie = secrets.token_hex(32)
        self.csrf_tokens = []
        self._auth_lock = threading.Lock()
        self.idle_seconds = idle_seconds
        self.last_activity = time.monotonic()
        self.runtime_path = runtime_path
        self._watcher_stop = threading.Event()
        super().__init__(address, SnapshotHandler)
        self.host = f"127.0.0.1:{self.server_port}"
        self.origin = "http://" + self.host
        if register:
            register(runtime_path)
        os.makedirs(os.path.dirname(runtime_path), mode=0o700, exist_ok=True)
        # O_EXCL prevents following a pre-existing foreign/symlink credential.
        fd = os.open(runtime_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"pid": os.getpid(), "port": self.server_port, "token": self.token,
                       "script": os.path.join(os.path.dirname(__file__), "token-dashboard.py")}, stream)

    @property
    def url(self):
        return self.origin + "#token=" + self.token

    def page_csrf(self):
        token = secrets.token_hex(32)
        with self._auth_lock:
            self.csrf_tokens.append(token)
            del self.csrf_tokens[:-256]
        return token

    def valid_csrf(self, token):
        with self._auth_lock:
            return any(hmac.compare_digest(token, value) for value in self.csrf_tokens)

    def idle_expired(self, now=None):
        return (time.monotonic() if now is None else now) - self.last_activity >= self.idle_seconds

    def serve_forever(self, poll_interval=.5):
        def watch():
            while not self._watcher_stop.wait(min(5, self.idle_seconds)):
                if self.idle_expired():
                    self.shutdown()
                    return
        threading.Thread(target=watch, daemon=True).start()
        return super().serve_forever(poll_interval)

    def server_close(self):
        self._watcher_stop.set()
        super().server_close()
        try:
            with open(self.runtime_path) as stream:
                record = json.load(stream)
            if record.get("token") == self.token and record.get("pid") == os.getpid():
                os.unlink(self.runtime_path)
        except (OSError, ValueError):
            pass


class SnapshotHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # request paths, fragments and credentials are never logged

    def _send(self, payload, status=200, html=False, cookie=None):
        nonce = secrets.token_hex(16)
        text = _nonce_page(payload, nonce) if html else json.dumps(payload)
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        hosts = self.headers.get_all("Host", [])
        if hosts != [self.server.host]:
            self._send({"error": "invalid Host"}, 403)
            return False
        self.server.last_activity = time.monotonic()
        return True

    def _authenticated(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
            value = cookies.get("prism_session")
            return value is not None and hmac.compare_digest(value.value, self.server.session_cookie)
        except (ValueError, TypeError):
            return False

    def _origin_ok(self):
        return self.headers.get_all("Origin", []) == [self.server.origin]

    def _snapshot(self):
        store = self.server.store_factory()
        try:
            payload = store.latest_snapshot() or {"loading": True, "message": "The first local collection is running."}
            return {**payload, "live_status": store.status(),
                    **({"github": self.server.interactive_github} if self.server.interactive_github is not None else {})}
        finally:
            store.close()

    def do_GET(self):
        if not self._host_ok():
            return
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/api/data":
                self._send(self._snapshot())
            elif parsed.path == "/api/status":
                store = self.server.store_factory()
                try:
                    self._send(store.status())
                finally:
                    store.close()
            elif parsed.path == "/api/search":
                if not self._authenticated():
                    self._send({"error": "authentication required"}, 401)
                    return
                if len(parsed.query) > 4096:
                    self._send({"error": "query too long"}, 400)
                    return
                query = parse_qs(parsed.query, max_num_fields=12)
                filters = {key: query[key][0] for key in ("tool", "project", "role", "date_from", "date_to") if key in query}
                filters["limit"] = max(1, min(50, int(query.get("limit", [20])[0])))
                store = self.server.store_factory()
                try:
                    self._send({"results": store.search(query.get("q", [""])[0], **filters)})
                finally:
                    store.close()
            elif parsed.path in ("/api/refresh", "/api/session"):
                self._send({"error": "POST required"}, 405)
            elif parsed.path == "/":
                self._send(self.server.page, html=True)
            else:
                self._send({"error": "not found"}, 404)
        except (ValueError, TypeError) as exc:
            self._send({"error": "invalid query"}, 400)
        except Exception:
            self._send({"error": "local data unavailable"}, 503)

    def do_POST(self):
        if not self._host_ok():
            return
        if not self._origin_ok():
            self._send({"error": "invalid Origin"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 4096 or self.headers.get("Transfer-Encoding"):
                raise ValueError("body limit")
            self.connection.settimeout(5)
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("object required")
        except (ValueError, OSError):
            self._send({"error": "invalid request"}, 400)
            return
        if self.path == "/api/session":
            token = data.get("token", "")
            if not self._authenticated() and not (isinstance(token, str) and hmac.compare_digest(token, self.server.token)):
                self._send({"error": "invalid credential"}, 401)
                return
            self._send({"csrf": self.server.page_csrf()}, cookie=f"prism_session={self.server.session_cookie}; HttpOnly; SameSite=Strict; Path=/")
        elif self.path in ("/api/refresh", "/api/github"):
            if not self._authenticated():
                self._send({"error": "authentication required"}, 401)
            elif not self.server.valid_csrf(self.headers.get("X-Prism-CSRF", "")):
                self._send({"error": "invalid CSRF"}, 403)
            else:
                if self.path == "/api/github":
                    if self.server.github is None:
                        self._send({"error": "interactive GitHub unavailable"}, 503)
                        return
                    self.server.interactive_github = self.server.github()
                else:
                    self.server.collect()
                self._send(self._snapshot())
        else:
            self._send({"error": "not found"}, 404)
