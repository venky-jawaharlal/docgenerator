"""Lightweight checks on a generated OpenAPI document and its deployment host."""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

from .servers import is_blocked_ping_host, is_localhost_url, normalize_server_url, redact_url

_PING_TIMEOUT_SEC = 8.0


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Treat 3xx as a final response so pings cannot be steered to a second hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def validate_openapi_doc(doc: dict[str, Any], *, require_remote_server: bool = True) -> list[str]:
    """Return human-readable issues. Empty list means the document looks usable."""
    issues: list[str] = []
    version = str(doc.get("openapi") or "")
    if not version.startswith("3."):
        issues.append("missing or non-3.x 'openapi' version field")
    info = doc.get("info") or {}
    if not info.get("title"):
        issues.append("info.title is missing")
    if not info.get("version"):
        issues.append("info.version is missing")
    paths = doc.get("paths")
    if not isinstance(paths, dict) or not paths:
        issues.append("no paths documented")

    servers = doc.get("servers") or []
    path_servers = []
    if isinstance(paths, dict):
        for item in paths.values():
            if isinstance(item, dict):
                path_servers.extend(item.get("servers") or [])

    all_servers = list(servers) + path_servers
    urls = [s.get("url", "") for s in all_servers if isinstance(s, dict)]
    if require_remote_server and not urls:
        issues.append(
            "no servers listed — Swagger UI Try-it-out will target this page's "
            "origin (often localhost). Set `host` or `servers` to the deployed service."
        )
    local = [u for u in urls if is_localhost_url(u)]
    remote = [u for u in urls if u and not is_localhost_url(u)]
    if require_remote_server and local and not remote:
        issues.append(
            "only localhost/loopback servers are listed "
            f"({', '.join(redact_url(u) for u in local)}); "
            "Try-it-out will not hit a deployed host"
        )

    schemes = ((doc.get("components") or {}).get("securitySchemes")) or {}
    referenced: set[str] = set()
    for req in doc.get("security") or []:
        if isinstance(req, dict):
            referenced.update(req.keys())
    if isinstance(paths, dict):
        for item in paths.values():
            if not isinstance(item, dict):
                continue
            for op in item.values():
                if not isinstance(op, dict):
                    continue
                for req in op.get("security") or []:
                    if isinstance(req, dict):
                        referenced.update(req.keys())
    missing = sorted(name for name in referenced if name not in schemes)
    if missing:
        issues.append(
            "operations reference security schemes that are not defined: "
            + ", ".join(missing)
        )
    return issues


def ping_url(
    url: str,
    timeout: float = _PING_TIMEOUT_SEC,
    extra_headers: dict[str, str] | None = None,
    method: str | None = None,
) -> tuple[bool, str]:
    """Reachability check against an operator-configured http(s) host.

    Does not follow redirects, does not probe cloud metadata, and never opens
    non-http schemes. Callers must pass configured hosts, not imported spec URLs.
    """
    target = normalize_server_url(url)
    safe = redact_url(target) or redact_url(url)
    if not target:
        return False, f"rejected (http/https only): {safe}"
    if is_localhost_url(target) or is_blocked_ping_host(target):
        return False, f"blocked (loopback/metadata): {safe}"

    opener = urllib.request.build_opener(_NoRedirectHandler)
    headers = {"User-Agent": "swagger_gen-host-check"}
    if extra_headers:
        headers.update(extra_headers)
    last = f"unreachable: {safe}"
    methods = (method.upper(),) if method else ("HEAD", "GET")
    for verb in methods:
        req = urllib.request.Request(target, method=verb, headers=headers)
        try:
            with opener.open(req, timeout=timeout) as resp:
                return True, f"{resp.status} {verb} {safe}"
        except urllib.error.HTTPError as exc:
            return True, f"{exc.code} {verb} {safe}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
    return False, last


def join_host_path(host: str, path: str) -> str:
    base = normalize_server_url(host).rstrip("/")
    suffix = "/" + (path or "").lstrip("/")
    if suffix == "/":
        return base or host
    return f"{base}{suffix}"
