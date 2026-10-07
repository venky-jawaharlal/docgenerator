"""Local Swagger UI server with a same-origin Try-it-out proxy.

Browsers block cross-origin Try-it-out (especially once Authorization is
sent) even when the API returned 200. Serving the HTML and forwarding
allowlisted requests from this process avoids CORS while still hitting the
configured deployment host.
"""

from __future__ import annotations

import json
import mimetypes
import posixpath
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .auth import AuthConfig, request_headers
from .config import Config
from .output import PROXY_BOOTSTRAP_OFF
from .servers import (
    is_blocked_ping_host,
    is_localhost_url,
    normalize_server_url,
    redact_url,
)

PROXY_PREFIX = "/__proxy__/"
PROXY_MARKER = PROXY_BOOTSTRAP_OFF
_MAX_BODY = 10 * 1024 * 1024
_TIMEOUT_SEC = 30.0
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "origin",
    "referer",
    "cookie",
}


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def origin_of(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def proxy_allowlist(config: Config) -> list[tuple[str, Optional[AuthConfig]]]:
    """Return (base_url, auth) pairs for operator-configured hosts only."""
    entries: list[tuple[str, Optional[AuthConfig]]] = []
    seen: set[str] = set()
    for repo in config.repos:
        urls = [s.url for s in repo.servers]
        if repo.auth and repo.auth.token_url:
            urls.append(repo.auth.token_url)
        for raw in urls:
            url = normalize_server_url(raw)
            if not url or url in seen:
                continue
            if is_localhost_url(url) or is_blocked_ping_host(url):
                continue
            seen.add(url)
            entries.append((url, repo.auth))
    return entries


def allowed_origins(entries: list[tuple[str, Optional[AuthConfig]]]) -> list[str]:
    origins: list[str] = []
    seen: set[str] = set()
    for url, _auth in entries:
        origin = origin_of(url)
        if origin and origin not in seen:
            seen.add(origin)
            origins.append(origin)
    return origins


def resolve_proxy_target(
    path: str,
    query: str,
    allowlist: list[tuple[str, Optional[AuthConfig]]],
) -> tuple[str, Optional[AuthConfig], str]:
    """Return (target, auth, error). error is empty on success."""
    rest = path[len(PROXY_PREFIX) :] if path.startswith(PROXY_PREFIX) else ""
    rest = unquote(rest)
    if not rest:
        return "", None, "missing proxy target"
    parts = rest.split("/", 2)
    if len(parts) < 2:
        return "", None, "invalid proxy target"
    scheme, netloc = parts[0].lower(), parts[1]
    if scheme not in ("http", "https") or not netloc or "/" in netloc or "@" in netloc:
        return "", None, "invalid proxy host"
    suffix = "/" + parts[2] if len(parts) > 2 else ""
    target = f"{scheme}://{netloc}{suffix}"
    if query:
        target = f"{target}?{query}"
    base = normalize_server_url(f"{scheme}://{netloc}{suffix.split('?', 1)[0]}")
    if not base:
        return "", None, "rejected proxy target"
    if is_localhost_url(base) or is_blocked_ping_host(base):
        return "", None, "blocked (loopback/metadata)"
    origin = origin_of(base)
    match_auth: Optional[AuthConfig] = None
    allowed = False
    best = -1
    for configured, auth in allowlist:
        conf_origin = origin_of(configured)
        if origin != conf_origin:
            continue
        allowed = True
        if base == configured or base.startswith(configured + "/") or configured == origin:
            if len(configured) > best:
                match_auth = auth
                best = len(configured)
        elif match_auth is None:
            match_auth = auth
    if not allowed:
        return "", None, f"host not in config allowlist: {redact_url(base)}"
    return target, match_auth, ""


def _bootstrap_script(origins: list[str]) -> str:
    hosts = json.dumps(origins)
    return (
        "window.SWAGGER_GEN_PROXY = {hosts: "
        + hosts
        + "};\n"
        "    (function(){\n"
        "      var b = document.createElement('div');\n"
        "      b.textContent = 'Try-it-out is proxied through this local server "
        "to avoid CORS. Requests still go to the deployed host.';\n"
        "      b.style.cssText = 'font:13px/1.4 -apple-system,sans-serif;"
        "background:#0f172a;color:#fde68a;padding:8px 16px;';\n"
        "      if (document.body) document.body.prepend(b);\n"
        "      else document.addEventListener('DOMContentLoaded', function(){"
        "document.body.prepend(b);});\n"
        "    })();"
    )


def _handler_class(
    root: Path,
    allowlist: list[tuple[str, Optional[AuthConfig]]],
    origins: list[str],
):
    root = root.resolve()
    bootstrap = _bootstrap_script(origins)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            print(fmt % args, file=sys.stderr)

        def do_OPTIONS(self) -> None:  # noqa: N802
            if self.path.startswith(PROXY_PREFIX):
                self.send_response(204)
                self.send_header("Allow", "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS")
                self.end_headers()
                return
            self._serve_static()

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith(PROXY_PREFIX):
                self._proxy()
                return
            self._serve_static()

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_POST(self) -> None:  # noqa: N802
            self._proxy_or_reject()

        def do_PUT(self) -> None:  # noqa: N802
            self._proxy_or_reject()

        def do_PATCH(self) -> None:  # noqa: N802
            self._proxy_or_reject()

        def do_DELETE(self) -> None:  # noqa: N802
            self._proxy_or_reject()

        def _proxy_or_reject(self) -> None:
            if self.path.startswith(PROXY_PREFIX):
                self._proxy()
                return
            self.send_error(404, "Not Found")

        def _serve_static(self) -> None:
            parsed = urlparse(self.path)
            rel = unquote(parsed.path).lstrip("/")
            if not rel or rel.endswith("/"):
                rel = (rel or "") + "index.html"
            rel = posixpath.normpath(rel)
            if rel.startswith("..") or "/../" in f"/{rel}/":
                self.send_error(403, "Forbidden")
                return
            candidate = (root / rel).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                self.send_error(403, "Forbidden")
                return
            if not candidate.is_file():
                self.send_error(404, "Not Found")
                return
            data = candidate.read_bytes()
            ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
            if candidate.suffix == ".html":
                text = data.decode("utf-8", "replace")
                if PROXY_MARKER in text:
                    text = text.replace(PROXY_MARKER, bootstrap, 1)
                    data = text.encode("utf-8")
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _proxy(self) -> None:
            parsed = urlparse(self.path)
            target, auth, error = resolve_proxy_target(
                parsed.path, parsed.query, allowlist
            )
            if error:
                body = error.encode("utf-8")
                self.send_response(403)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return

            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self.send_error(413, "Payload too large")
                return
            payload = self.rfile.read(length) if length else None

            headers = {"User-Agent": "swagger_gen-try-it-out-proxy"}
            for key, value in self.headers.items():
                if key.lower() in _HOP_BY_HOP:
                    continue
                if key.lower() in {"user-agent"}:
                    continue
                headers[key] = value
            extra = request_headers(auth)
            for key, value in extra.items():
                if not headers.get(key):
                    headers[key] = value

            opener = build_opener(_NoRedirectHandler)
            req = Request(target, data=payload, method=self.command, headers=headers)
            try:
                with opener.open(req, timeout=_TIMEOUT_SEC) as resp:
                    raw = resp.read()
                    status = getattr(resp, "status", 200)
                    upstream_headers = resp.headers
            except HTTPError as exc:
                raw = exc.read() or b""
                status = exc.code
                upstream_headers = exc.headers
            except Exception as exc:
                msg = f"upstream error: {type(exc).__name__}".encode("utf-8")
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.send_header("X-Swagger-Gen-Upstream", redact_url(target))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(msg)
                return

            self.send_response(status)
            self.send_header("X-Swagger-Gen-Upstream", redact_url(target))
            if upstream_headers:
                for key, value in upstream_headers.items():
                    if key.lower() in _HOP_BY_HOP | {"set-cookie", "set-cookie2"}:
                        continue
                    if key.lower() == "content-length":
                        continue
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)

    return Handler


def serve_docs(
    out_dir: Path,
    config: Config,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    allowlist = proxy_allowlist(config)
    origins = allowed_origins(allowlist)
    if not origins:
        print(
            "warning: --serve proxy has no allowlisted hosts; "
            "set host/servers in config so Try-it-out can be forwarded.",
            file=sys.stderr,
        )
    handler = _handler_class(out_dir, allowlist, origins)
    httpd = ThreadingHTTPServer((host, port), handler)
    loc = f"http://{host}:{port}/"
    print(f"\nSwagger UI (CORS proxy): {loc}")
    print("Try-it-out is forwarded to configured deployment hosts from this process.")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped local docs server.")
    finally:
        httpd.server_close()
