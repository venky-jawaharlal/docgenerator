"""Write generated OpenAPI documents to disk in the requested formats."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import yaml

SWAGGER_UI_VERSION = "5.17.14"
PROXY_BOOTSTRAP_OFF = "/* try-it-out proxy off */"

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; script-src https://unpkg.com 'unsafe-inline'; style-src https://unpkg.com 'unsafe-inline'; img-src data: https:; connect-src 'self' https: http:; font-src https://unpkg.com" />
  <meta http-equiv="Referrer-Policy" content="no-referrer" />
  <title>__TITLE__ - API Docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@__VER__/swagger-ui.css" />
  <style>
    body { margin: 0; background: #f4f6fb; }
    .sg-chrome {
      display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
      padding: 10px 18px; background: #0b1220; color: #e2e8f0;
      font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .sg-chrome a { color: #7dd3fc; text-decoration: none; font-weight: 600; }
    .sg-chrome a:hover { text-decoration: underline; }
    .sg-chrome strong { font-size: 14px; }
    .sg-chrome .hint { color: #94a3b8; margin-left: auto; }
    .swagger-ui .topbar { display: none; }
  </style>
</head>
<body>
  <nav class="sg-chrome">
    <a href="index.html">All projects</a>
    <strong>__TITLE__</strong>
    <span class="hint">Grouped by resource · filter in the bar below</span>
  </nav>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@__VER__/swagger-ui-bundle.js"></script>
  <script src="https://unpkg.com/swagger-ui-dist@__VER__/swagger-ui-standalone-preset.js"></script>
  <script>
    const spec = __SPEC_JSON__;
    const authPrefill = __AUTH_PREFILL__;
    __PROXY_BOOTSTRAP__
    window.ui = SwaggerUIBundle({
      spec: spec,
      dom_id: '#swagger-ui',
      deepLinking: true,
      persistAuthorization: false,
      tryItOutEnabled: true,
      docExpansion: 'list',
      filter: true,
      tagsSorter: 'alpha',
      operationsSorter: 'method',
      defaultModelsExpandDepth: -1,
      displayRequestDuration: true,
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
        if (window.SWAGGER_GEN_PROXY && window.SWAGGER_GEN_PROXY.hosts && req.url) {
          try {
            var parsed = new URL(req.url, window.location.href);
            if (parsed.pathname.indexOf('/__proxy__/') === 0) return req;
            if (window.SWAGGER_GEN_PROXY.hosts.indexOf(parsed.origin) !== -1) {
              req.url = window.location.origin + '/__proxy__/'
                + parsed.protocol.replace(':','') + '/'
                + parsed.host + parsed.pathname + parsed.search;
            }
          } catch (e) {}
        }
        return req;
      }
    });
  </script>
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
            .replace("__PROXY_BOOTSTRAP__", PROXY_BOOTSTRAP_OFF)
        )
        path.write_text(html_page, encoding="utf-8")
        written["html"] = path

    return written


def write_index(
    out_dir: Path,
    entries: list[dict[str, Any]],
) -> Path:
    """Write the project-grouped API portal to index.html."""
    from .portal import write_portal

    return write_portal(out_dir, entries)
