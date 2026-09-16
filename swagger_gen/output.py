"""Write generated OpenAPI documents to disk in the requested formats."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import yaml

SWAGGER_UI_VERSION = "5.17.14"

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; script-src https://unpkg.com 'unsafe-inline'; style-src https://unpkg.com 'unsafe-inline'; img-src data: https:; connect-src https: http:; font-src https://unpkg.com" />
  <meta http-equiv="Referrer-Policy" content="no-referrer" />
  <title>__TITLE__ - API Docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@__VER__/swagger-ui.css" />
  <style>body { margin: 0; background: #fafafa; }</style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@__VER__/swagger-ui-bundle.js"></script>
  <script src="https://unpkg.com/swagger-ui-dist@__VER__/swagger-ui-standalone-preset.js"></script>
  <script>
    const spec = __SPEC_JSON__;
    const authPrefill = __AUTH_PREFILL__;
    window.ui = SwaggerUIBundle({
      spec: spec,
      dom_id: '#swagger-ui',
      deepLinking: true,
      persistAuthorization: false,
      tryItOutEnabled: true,
      presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
      layout: 'BaseLayout',
      validatorUrl: null,
      onComplete: function() {
        if (!authPrefill || !authPrefill.schemes || !window.ui) return;
        Object.keys(authPrefill.schemes).forEach(function(name) {
          var cfg = authPrefill.schemes[name];
          if (cfg.type === 'basic') {
            window.ui.preauthorizeBasic(name, cfg.username || '', cfg.password || '');
          } else if (cfg.value) {
            window.ui.preauthorizeApiKey(name, cfg.value);
          }
        });
      },
      requestInterceptor: function(req) {
        if (authPrefill && authPrefill.header && authPrefill.header.name) {
          req.headers = req.headers || {};
          if (!req.headers[authPrefill.header.name]) {
            req.headers[authPrefill.header.name] = authPrefill.header.value;
          }
        }
        return req;
      }
    });
  </script>
</body>
</html>
"""

_INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src 'unsafe-inline'" />
  <title>API Documentation Index</title>
  <style>
    body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
           margin: 0; background: #0f172a; color: #e2e8f0; }
    header { padding: 32px 40px; background: #111827; border-bottom: 1px solid #1f2937; }
    h1 { margin: 0; font-size: 22px; }
    p.sub { color: #94a3b8; margin: 6px 0 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 16px; padding: 32px 40px; }
    a.card { display: block; text-decoration: none; color: inherit; background: #1e293b;
             border: 1px solid #334155; border-radius: 12px; padding: 20px;
             transition: transform .08s ease, border-color .08s ease; }
    a.card:hover { transform: translateY(-2px); border-color: #3b82f6; }
    .name { font-size: 17px; font-weight: 600; }
    .meta { color: #94a3b8; font-size: 13px; margin-top: 8px; }
    .tags { margin-top: 12px; }
    .tag { display: inline-block; background: #0b3b6f; color: #bfdbfe; font-size: 12px;
           padding: 2px 8px; border-radius: 999px; margin: 2px 4px 0 0; }
    .count { color: #34d399; font-weight: 600; }
    .host { color: #7dd3fc; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: 12px; margin-top: 10px; word-break: break-all; }
    .auth { color: #fcd34d; font-size: 12px; margin-top: 6px; }
  </style>
</head>
<body>
  <header>
    <h1>API Documentation</h1>
    <p class="sub">__SUMMARY__</p>
  </header>
  <div class="grid">
    __CARDS__
  </div>
</body>
</html>
"""

_SAFE_HREF = re.compile(r"^[A-Za-z0-9._-]+\.html$")


def _slug(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
    return safe.strip("-").lower() or "repo"


def _clip(value: Any, limit: int = 300) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _esc(value: Any, limit: int = 300) -> str:
    return html.escape(_clip(value, limit), quote=True)


def _spec_json(doc: dict[str, Any]) -> str:
    # Neutralize </script> breakout inside the inline JSON payload.
    return (
        json.dumps(doc)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def write_spec(
    doc: dict[str, Any],
    out_dir: Path,
    base_name: str,
    formats: list[str],
    auth_prefill: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Write ``doc`` in each requested format. Returns {format: path}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    slug = _slug(base_name)

    if "json" in formats:
        path = out_dir / f"{slug}.openapi.json"
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        written["json"] = path

    if "yaml" in formats or "yml" in formats:
        path = out_dir / f"{slug}.openapi.yaml"
        path.write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        written["yaml"] = path

    if "html" in formats:
        path = out_dir / f"{slug}.html"
        title = _esc(doc.get("info", {}).get("title", base_name), 200)
        html_page = (
            _HTML_TEMPLATE.replace("__TITLE__", title)
            .replace("__VER__", SWAGGER_UI_VERSION)
            .replace("__SPEC_JSON__", _spec_json(doc))
            .replace("__AUTH_PREFILL__", _spec_json(auth_prefill or {}))
        )
        path.write_text(html_page, encoding="utf-8")
        written["html"] = path

    return written


def write_index(
    out_dir: Path,
    entries: list[dict[str, Any]],
) -> Path:
    """Write an index.html linking every per-repo HTML doc."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cards = []
    total_endpoints = 0
    for entry in entries:
        total_endpoints += entry["endpoint_count"]
        tags = "".join(
            f'<span class="tag">{_esc(t, 80)}</span>' for t in entry.get("frameworks", [])
        )
        href = entry.get("html") or ""
        if href and not _SAFE_HREF.fullmatch(str(href)):
            href = ""
        title = _esc(entry.get("title"), 200)
        version = _esc(entry.get("version"), 40)
        hosts = entry.get("servers") or []
        host_html = (
            f'<div class="host">{_esc(hosts[0], 200)}</div>' if hosts else
            '<div class="host">No deployment host configured</div>'
        )
        auth_label = entry.get("auth") or ""
        auth_html = f'<div class="auth">Auth: {_esc(auth_label, 80)}</div>' if auth_label else ""
        card_inner = (
            f'<div class="name">{title}</div>'
            f'<div class="meta"><span class="count">{int(entry["endpoint_count"])}</span>'
            f' endpoints &middot; v{version}</div>'
            f'{host_html}'
            f'{auth_html}'
            f'<div class="tags">{tags}</div>'
        )
        if href:
            cards.append(f'<a class="card" href="{html.escape(href, quote=True)}">{card_inner}</a>')
        else:
            cards.append(f'<div class="card">{card_inner}</div>')

    summary = (
        f"{len(entries)} service(s) &middot; {total_endpoints} endpoints documented"
    )
    html_page = (
        _INDEX_TEMPLATE.replace("__SUMMARY__", _esc(summary, 200))
        .replace("__CARDS__", "\n    ".join(cards))
    )
    path = out_dir / "index.html"
    path.write_text(html_page, encoding="utf-8")
    return path
