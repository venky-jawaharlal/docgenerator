"""Write generated OpenAPI documents to disk in the requested formats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

SWAGGER_UI_VERSION = "5.17.14"

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} - API Docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@{ver}/swagger-ui.css" />
  <style>body {{ margin: 0; background: #fafafa; }}</style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@{ver}/swagger-ui-bundle.js"></script>
  <script src="https://unpkg.com/swagger-ui-dist@{ver}/swagger-ui-standalone-preset.js"></script>
  <script>
    const spec = {spec_json};
    window.ui = SwaggerUIBundle({{
      spec: spec,
      dom_id: '#swagger-ui',
      deepLinking: true,
      presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
      layout: 'BaseLayout'
    }});
  </script>
</body>
</html>
"""

_INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>API Documentation Index</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
           margin: 0; background: #0f172a; color: #e2e8f0; }}
    header {{ padding: 32px 40px; background: #111827; border-bottom: 1px solid #1f2937; }}
    h1 {{ margin: 0; font-size: 22px; }}
    p.sub {{ color: #94a3b8; margin: 6px 0 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 16px; padding: 32px 40px; }}
    a.card {{ display: block; text-decoration: none; color: inherit; background: #1e293b;
             border: 1px solid #334155; border-radius: 12px; padding: 20px;
             transition: transform .08s ease, border-color .08s ease; }}
    a.card:hover {{ transform: translateY(-2px); border-color: #3b82f6; }}
    .name {{ font-size: 17px; font-weight: 600; }}
    .meta {{ color: #94a3b8; font-size: 13px; margin-top: 8px; }}
    .tags {{ margin-top: 12px; }}
    .tag {{ display: inline-block; background: #0b3b6f; color: #bfdbfe; font-size: 12px;
           padding: 2px 8px; border-radius: 999px; margin: 2px 4px 0 0; }}
    .count {{ color: #34d399; font-weight: 600; }}
  </style>
</head>
<body>
  <header>
    <h1>API Documentation</h1>
    <p class="sub">{summary}</p>
  </header>
  <div class="grid">
    {cards}
  </div>
</body>
</html>
"""


def _slug(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
    return safe.strip("-").lower() or "repo"


def write_spec(
    doc: dict[str, Any],
    out_dir: Path,
    base_name: str,
    formats: list[str],
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
        html = _HTML_TEMPLATE.format(
            title=doc.get("info", {}).get("title", base_name),
            ver=SWAGGER_UI_VERSION,
            spec_json=json.dumps(doc),
        )
        path.write_text(html, encoding="utf-8")
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
            f'<span class="tag">{t}</span>' for t in entry.get("frameworks", [])
        )
        href = entry.get("html")
        title = entry["title"]
        card_inner = (
            f'<div class="name">{title}</div>'
            f'<div class="meta"><span class="count">{entry["endpoint_count"]}</span>'
            f' endpoints &middot; v{entry["version"]}</div>'
            f'<div class="tags">{tags}</div>'
        )
        if href:
            cards.append(f'<a class="card" href="{href}">{card_inner}</a>')
        else:
            cards.append(f'<div class="card">{card_inner}</div>')

    summary = (
        f"{len(entries)} service(s) &middot; {total_endpoints} endpoints documented"
    )
    html = _INDEX_TEMPLATE.format(summary=summary, cards="\n    ".join(cards))
    path = out_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path
