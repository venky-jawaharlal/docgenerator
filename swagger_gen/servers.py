"""Deployment-host helpers for OpenAPI ``servers``.

Swagger UI sends Try-it-out / validation requests to whatever is listed under
``servers``. If that list is missing, the UI falls back to the page origin
(typically localhost). This module normalizes configured hosts, drops
localhost placeholders imported from source specs, and never invents a
localhost default.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from .models import Server

LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
ALLOWED_SERVER_SCHEMES = {"http", "https"}
# Cloud metadata endpoints — never ping these even if a spec lists them.
BLOCKED_PING_HOSTS = {
    "metadata.google.internal",
    "metadata.google.com",
    "metadata",
    "169.254.169.254",
    "fd00:ec2::254",
}


def is_localhost_url(url: str) -> bool:
    """True when ``url`` targets a loopback / unspecified local address."""
    raw = (url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "").lower().strip("[]")
    return host in LOCAL_HOSTNAMES or host.endswith(".localhost")


def normalize_server_url(url: str) -> str:
    """Return an absolute http(s) URL, or empty if the value is not usable."""
    raw = (url or "").strip().rstrip("/")
    if not raw or raw.startswith("-") or "\n" in raw or "\x00" in raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    elif "://" not in raw:
        # host[:port][/path] only. Reject javascript:, data:, mailto:, etc.
        hostport = raw.split("/", 1)[0]
        if ":" in hostport:
            port = hostport.rsplit(":", 1)[-1]
            if not port.isdigit():
                return ""
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ALLOWED_SERVER_SCHEMES:
        return ""
    host = parsed.hostname
    if not host:
        return ""
    # Never embed credentials in generated specs.
    if parsed.username or parsed.password:
        netloc = host
        if parsed.port:
            netloc = f"{host}:{parsed.port}"
        raw = urlunparse(parsed._replace(netloc=netloc))
    return raw.rstrip("/")


def redact_url(url: str) -> str:
    """Strip userinfo (tokens/passwords) from a URL before logging it."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if parsed.username or parsed.password:
            host = parsed.hostname or ""
            netloc = host
            if parsed.port:
                netloc = f"{host}:{parsed.port}"
            netloc = "****@" + netloc
            return urlunparse(parsed._replace(netloc=netloc))
    except ValueError:
        pass
    return re.sub(r"://[^/\s:@]+:[^/\s@]+@", "://****@", raw)


def is_blocked_ping_host(url: str) -> bool:
    """True for cloud-metadata / link-local targets that must never be probed."""
    import ipaddress

    parsed = urlparse(url if "://" in (url or "") else f"http://{url}")
    host = (parsed.hostname or "").lower().strip("[]")
    if not host:
        return True
    if host in BLOCKED_PING_HOSTS or host.endswith(".metadata.google.internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast)


def parse_server_entries(raw) -> list[Server]:
    """Accept a string, a list of strings, or a list of ``{url, description}`` maps."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    servers: list[Server] = []
    seen: set[str] = set()
    for item in raw:
        url = ""
        description = ""
        if isinstance(item, str):
            url = item
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("host") or "")
            description = str(item.get("description") or "")
        url = normalize_server_url(url)
        if not url or url in seen:
            continue
        seen.add(url)
        servers.append(Server(url=url, description=description))
    return servers


def servers_from_host(host: str | None, description: str = "Deployed service") -> list[Server]:
    if not host:
        return []
    url = normalize_server_url(str(host))
    if not url:
        return []
    return [Server(url=url, description=description)]


def merge_servers(*groups: list[Server]) -> list[Server]:
    """Concatenate server groups, first occurrence of each URL wins."""
    merged: list[Server] = []
    seen: set[str] = set()
    for group in groups:
        for server in group:
            url = normalize_server_url(server.url)
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(
                Server(url=url, description=server.description or "")
            )
    return merged


def drop_localhost(servers: list[Server]) -> tuple[list[Server], list[Server]]:
    """Split into (non-local, local) preserving order."""
    keep: list[Server] = []
    dropped: list[Server] = []
    for server in servers:
        if is_localhost_url(server.url):
            dropped.append(server)
        else:
            keep.append(server)
    return keep, dropped


def to_openapi(servers: list[Server]) -> list[dict]:
    nodes = []
    for server in servers:
        node: dict = {"url": server.url}
        if server.description:
            node["description"] = server.description
        nodes.append(node)
    return nodes
