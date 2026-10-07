"""Browsable API catalog grouped by sub-project."""

from __future__ import annotations

import html
from typing import Any

from .output import (
    PROXY_BOOTSTRAP_OFF,
    SWAGGER_UI_VERSION,
    _SAFE_HREF,
    _slug,
    _spec_json,
)
from .tags import subproject_name

_METHOD_ORDER = ("get", "post", "put", "patch", "delete", "head", "options")

_PORTAL_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; script-src https://unpkg.com 'unsafe-inline'; style-src https://unpkg.com 'unsafe-inline'; img-src data: https:; connect-src 'self' https: http:; font-src https://unpkg.com; frame-src 'self'" />
  <meta http-equiv="Referrer-Policy" content="no-referrer" />
  <title>API Documentation</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@__VER__/swagger-ui.css" />
  <style>
    :root {
      --bg: #0b1220;
      --panel: #111827;
      --panel-2: #1e293b;
      --line: #334155;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --accent-2: #818cf8;
      --ok: #34d399;
      --warn: #fbbf24;
      --main-bg: #f4f6fb;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .app { display: flex; height: 100%; min-height: 100vh; }
    .sidebar {
      width: 300px; flex: 0 0 300px;
      background: var(--panel);
      border-right: 1px solid var(--line);
      display: flex; flex-direction: column;
      min-height: 100vh;
    }
    .brand { padding: 20px 18px 12px; border-bottom: 1px solid var(--line); }
    .brand h1 { margin: 0; font-size: 16px; letter-spacing: .02em; }
    .brand p { margin: 6px 0 0; color: var(--muted); font-size: 12px; }
    .search-wrap { padding: 12px 14px 8px; }
    .search-wrap input {
      width: 100%; border: 1px solid var(--line); background: #0f172a;
      color: var(--text); border-radius: 8px; padding: 9px 11px; font-size: 13px;
    }
    .search-wrap input:focus { outline: 2px solid var(--accent); border-color: transparent; }
    .nav {
      overflow: auto; flex: 1; padding: 6px 10px 16px;
    }
    .nav a.svc {
      display: flex; align-items: flex-start; justify-content: space-between;
      gap: 8px; text-decoration: none; color: inherit;
      padding: 10px 10px; border-radius: 8px; margin-bottom: 4px;
    }
    .nav a.svc:hover { background: var(--panel-2); }
    .nav a.svc.active { background: #1d4ed8; color: #fff; }
    .nav a.svc.active .meta, .nav a.svc.active .host-s { color: #dbeafe; }
    .svc .name { font-weight: 600; font-size: 13px; }
    .svc .meta { color: var(--muted); font-size: 11px; margin-top: 2px; }
    .svc .count {
      background: #0f172a; color: var(--ok); font-size: 11px; font-weight: 700;
      border-radius: 999px; padding: 2px 8px; white-space: nowrap;
    }
    .nav a.svc.active .count { background: #1e3a8a; color: #bbf7d0; }
    .tree { margin-bottom: 8px; }
    .subs { margin: 0 0 6px 10px; padding-left: 10px; border-left: 1px solid #334155; }
    .nav a.sub {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      text-decoration: none; color: #cbd5e1; font-size: 13px;
      padding: 6px 8px; border-radius: 6px;
    }
    .nav a.sub:hover { background: var(--panel-2); color: #fff; }
    .nav a.sub.active { background: #1e3a8a; color: #fff; }
    .nav a.sub .count {
      color: #94a3b8; font-size: 11px; font-weight: 700;
    }
    .nav a.sub.active .count { color: #bbf7d0; }
    .overview-link {
      display: block; margin: 0 10px 8px; padding: 8px 10px; border-radius: 8px;
      color: var(--accent); text-decoration: none; font-size: 13px; font-weight: 600;
    }
    .overview-link:hover, .overview-link.active { background: var(--panel-2); }
    .main {
      flex: 1; background: var(--main-bg); color: #0f172a;
      overflow: auto; min-width: 0;
    }
    .topbar {
      position: sticky; top: 0; z-index: 4;
      display: flex; align-items: center; gap: 12px;
      padding: 12px 20px; background: #fff;
      border-bottom: 1px solid #e2e8f0;
    }
    .menu-btn {
      display: none; border: 1px solid #cbd5e1; background: #fff;
      border-radius: 8px; padding: 6px 10px; cursor: pointer;
    }
    .crumb { font-size: 14px; font-weight: 650; }
    .crumb span { color: #64748b; font-weight: 500; }
    .pill {
      font-size: 11px; font-weight: 600; border-radius: 999px;
      padding: 3px 8px; background: #e0f2fe; color: #075985;
    }
    .pill.auth { background: #fef3c7; color: #92400e; }
    .content { padding: 20px 24px 48px; }
    .hero { margin-bottom: 18px; }
    .hero h2 { margin: 0 0 6px; font-size: 22px; }
    .hero p { margin: 0; color: #64748b; }
    .project {
      background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
      margin-bottom: 14px; overflow: hidden;
    }
    .summary-row {
      display: flex; align-items: center; justify-content: space-between;
      gap: 12px; padding: 14px 16px;
    }
    .project .ptitle { font-weight: 700; }
    .project .psub { color: #64748b; font-size: 12px; margin-top: 3px; }
    .ops { border-top: 1px solid #e2e8f0; }
    .op {
      display: grid; grid-template-columns: 72px 1fr;
      gap: 10px; align-items: center;
      padding: 8px 16px; text-decoration: none; color: inherit;
      border-top: 1px solid #f1f5f9; font-size: 13px;
    }
    .op:hover { background: #f8fafc; }
    .verb {
      font: 700 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
      text-align: center; border-radius: 4px; padding: 5px 0; color: #fff;
    }
    .verb.GET { background: #059669; }
    .verb.POST { background: #2563eb; }
    .verb.PUT { background: #d97706; }
    .verb.PATCH { background: #7c3aed; }
    .verb.DELETE { background: #dc2626; }
    .verb.HEAD, .verb.OPTIONS { background: #475569; }
    .path { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
    .sum { color: #64748b; font-size: 12px; margin-top: 2px; }
    a.tag-h { display: block; text-decoration: none; }
    .tag-h {
      padding: 8px 16px 4px; font-size: 11px; font-weight: 700;
      text-transform: uppercase; letter-spacing: .06em; color: #64748b;
      background: #f8fafc; border-top: 1px solid #e2e8f0;
    }
    a.tag-h:hover { color: #1d4ed8; }
    .empty { color: #64748b; padding: 24px; text-align: center; }
    .swagger-wrap { background: #fff; border-radius: 12px; border: 1px solid #e2e8f0; }
    .swagger-wrap .swagger-ui { font-family: inherit; }
    .swagger-ui .topbar { display: none; }
    .swagger-ui .information-container { padding-top: 8px; }
    @media (max-width: 900px) {
      .sidebar {
        position: fixed; z-index: 8; transform: translateX(-105%);
        transition: transform .18s ease; height: 100%;
      }
      .sidebar.open { transform: none; }
      .menu-btn { display: inline-block; }
      .backdrop {
        display: none; position: fixed; inset: 0; background: rgba(15,23,42,.45); z-index: 7;
      }
      .backdrop.show { display: block; }
    }
  </style>
</head>
<body>
  <div class="backdrop" id="backdrop"></div>
  <div class="app">
    <aside class="sidebar" id="sidebar">
      <div class="brand">
        <h1>API documentation</h1>
        <p id="summary"></p>
      </div>
      <div class="search-wrap">
        <input id="q" type="search" placeholder="Search APIs, paths, services" autocomplete="off" />
      </div>
      <a class="overview-link active" id="overviewLink" href="#overview">All projects</a>
      <nav class="nav" id="nav"></nav>
    </aside>
    <section class="main">
      <div class="topbar">
        <button class="menu-btn" id="menuBtn" type="button">Menu</button>
        <div class="crumb" id="crumb">All projects</div>
        <div id="pills"></div>
      </div>
      <div class="content" id="content"></div>
    </section>
  </div>
  <script src="https://unpkg.com/swagger-ui-dist@__VER__/swagger-ui-bundle.js"></script>
  <script>
    const catalog = __CATALOG_JSON__;
    __PROXY_BOOTSTRAP__
    const nav = document.getElementById('nav');
    const content = document.getElementById('content');
    const crumb = document.getElementById('crumb');
    const pills = document.getElementById('pills');
    const q = document.getElementById('q');
    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('backdrop');
    const overviewLink = document.getElementById('overviewLink');
    let ui = null;

    document.getElementById('summary').textContent =
      (catalog.services || []).length + ' project(s) · ' +
      (catalog.endpoint_count || 0) + ' endpoints';

    function closeMenu() {
      sidebar.classList.remove('open');
      backdrop.classList.remove('show');
    }
    document.getElementById('menuBtn').onclick = function() {
      sidebar.classList.add('open');
      backdrop.classList.add('show');
    };
    backdrop.onclick = closeMenu;

    function methodOrder(m) {
      const i = ['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'].indexOf(m);
      return i < 0 ? 99 : i;
    }

    function matches(svc, op, query) {
      if (!query) return true;
      const blob = [svc.title, svc.name, svc.auth, (svc.frameworks||[]).join(' '),
        (svc.servers||[]).join(' '), op && op.method, op && op.path, op && op.summary,
        op && op.tag].join(' ').toLowerCase();
      return blob.indexOf(query) !== -1;
    }

    function groupsFor(svc, query) {
      const map = {};
      (svc.operations || []).forEach(function(op) {
        if (!matches(svc, op, query)) return;
        const id = op.groupId || 'api';
        if (!map[id]) map[id] = { id: id, name: op.group || 'API', count: 0 };
        map[id].count += 1;
      });
      return Object.keys(map).map(function(k) { return map[k]; }).sort(function(a, b) {
        return a.name.localeCompare(b.name);
      });
    }

    function renderNav(query) {
      const route = currentRoute();
      const html = [];
      (catalog.services || []).forEach(function(svc) {
        const groups = groupsFor(svc, query);
        if (query && !groups.length && !matches(svc, null, query)) return;
        const n = query
          ? groups.reduce(function(s, g) { return s + g.count; }, 0)
          : svc.endpoint_count;
        html.push('<div class="tree">');
        html.push(
          '<a class="svc" data-id="' + svc.id + '" href="#' + encodeURIComponent(svc.id) + '">' +
            '<div><div class="name"></div><div class="meta"></div></div>' +
            '<span class="count">' + n + '</span></a>'
        );
        html.push('<div class="subs">');
        groups.forEach(function(g) {
          html.push(
            '<a class="sub" data-id="' + svc.id + '" data-group="' + g.id + '" href="#' +
            encodeURIComponent(svc.id) + '/' + encodeURIComponent(g.id) + '">' +
            '<span class="name"></span><span class="count">' + g.count + '</span></a>'
          );
        });
        html.push('</div></div>');
      });
      nav.innerHTML = html.join('') || '<div class="empty">No matching projects</div>';
      let gi = 0;
      (catalog.services || []).forEach(function(svc) {
        const groups = groupsFor(svc, query);
        if (query && !groups.length && !matches(svc, null, query)) return;
        const trees = nav.querySelectorAll('.tree');
        const tree = trees[gi++];
        if (!tree) return;
        const parent = tree.querySelector('a.svc');
        parent.querySelector('.name').textContent = svc.title;
        parent.querySelector('.meta').textContent = (svc.frameworks || []).join(', ') || 'API';
        parent.classList.toggle('active', route.svc === svc.id && !route.group);
        const links = tree.querySelectorAll('a.sub');
        groups.forEach(function(g, i) {
          if (!links[i]) return;
          links[i].querySelector('.name').textContent = g.name;
          links[i].classList.toggle('active', route.svc === svc.id && route.group === g.id);
        });
      });
    }

    function groupedOps(svc, query) {
      const groups = {};
      (svc.operations || []).forEach(function(op) {
        if (!matches(svc, op, query)) return;
        const id = op.groupId || 'api';
        if (!groups[id]) groups[id] = { id: id, name: op.group || 'API', ops: [] };
        groups[id].ops.push(op);
      });
      Object.keys(groups).forEach(function(id) {
        groups[id].ops.sort(function(a, b) {
          const d = methodOrder(a.method) - methodOrder(b.method);
          return d !== 0 ? d : (a.path || '').localeCompare(b.path || '');
        });
      });
      return groups;
    }

    function opRow(svc, op) {
      const href = '#' + encodeURIComponent(svc.id) + '/' + encodeURIComponent(op.groupId || 'api');
      return '<a class="op" href="' + href + '">' +
        '<span class="verb ' + op.method + '">' + op.method + '</span>' +
        '<span><div class="path"></div><div class="sum"></div></span></a>';
    }

    function renderOverview(query) {
      overviewLink.classList.add('active');
      Array.prototype.forEach.call(nav.querySelectorAll('a.svc'), function(a) {
        a.classList.remove('active');
      });
      crumb.innerHTML = query ? 'Search results' : 'All projects';
      pills.innerHTML = '';
      const parts = [];
      if (!query) {
        parts.push('<div class="hero"><h2>Projects</h2>' +
          '<p>Each repo lists its sub-projects in the left pane. Open one to try its APIs.</p></div>');
      }
      let any = false;
      (catalog.services || []).forEach(function(svc) {
        const groups = groupedOps(svc, query);
        const tags = Object.keys(groups).sort(function(a, b) {
          return groups[a].name.localeCompare(groups[b].name);
        });
        if (!tags.length) return;
        any = true;
        const count = tags.reduce(function(n, t) { return n + groups[t].ops.length; }, 0);
        let body = '';
        tags.forEach(function(tag) {
          body += '<div class="tag-h"></div>';
          groups[tag].ops.forEach(function(op) { body += opRow(svc, op); });
        });
        parts.push('<section class="project">' +
          '<div class="summary-row"><div><div class="ptitle"></div><div class="psub"></div></div>' +
          '<span class="pill">' + count + ' APIs</span></div>' +
          '<div class="ops">' + body + '</div></section>');
      });
      content.innerHTML = parts.join('') || '<div class="empty">No endpoints match that search.</div>';
      const blocks = content.querySelectorAll('.project');
      let bi = 0;
      (catalog.services || []).forEach(function(svc) {
        const groups = groupedOps(svc, query);
        const tags = Object.keys(groups);
        if (!tags.length) return;
        const block = blocks[bi++];
        block.querySelector('.ptitle').textContent = svc.title;
        const host = (svc.servers && svc.servers[0]) || '';
        block.querySelector('.psub').textContent =
          [(svc.frameworks || [])[0] || '', host].filter(Boolean).join(' · ');
        const heads = block.querySelectorAll('.tag-h');
        tags.sort(function(a, b) { return groups[a].name.localeCompare(groups[b].name); }).forEach(function(tag, i) {
          heads[i].textContent = groups[tag].name;
          const rows = [];
          let node = heads[i].nextElementSibling;
          while (node && node.classList.contains('op')) { rows.push(node); node = node.nextElementSibling; }
          groups[tag].ops.forEach(function(op, j) {
            if (!rows[j]) return;
            rows[j].querySelector('.path').textContent = op.path;
            rows[j].querySelector('.sum').textContent = op.summary || '';
          });
        });
      });
    }

    function destroyUi() {
      if (ui && ui.getSystem) {
        try { document.getElementById('swagger-ui') && (document.getElementById('swagger-ui').innerHTML = ''); }
        catch (e) {}
      }
      ui = null;
    }

    function mountSwagger(spec, authPrefill) {
      destroyUi();
      content.innerHTML = '<div class="swagger-wrap"><div id="swagger-ui"></div></div>';
      if (spec && spec.info) {
        spec = JSON.parse(JSON.stringify(spec));
        var desc = String(spec.info.description || '');
        spec.info.description = desc.split('\\n\\nGenerator notes:')[0];
      }
      ui = SwaggerUIBundle({
        spec: spec,
        dom_id: '#swagger-ui',
        deepLinking: false,
        persistAuthorization: false,
        tryItOutEnabled: true,
        docExpansion: 'list',
        filter: true,
        tagsSorter: 'alpha',
        operationsSorter: 'method',
        defaultModelsExpandDepth: -1,
        displayRequestDuration: true,
        presets: [SwaggerUIBundle.presets.apis],
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
      window.ui = ui;
    }

    function filterSpec(spec, groupName, pathSet) {
      const copy = JSON.parse(JSON.stringify(spec));
      const keep = {};
      Object.keys(copy.paths || {}).forEach(function(p) {
        if (!pathSet[p]) return;
        const item = copy.paths[p];
        Object.keys(item).forEach(function(method) {
          const op = item[method];
          if (!op || typeof op !== 'object' || !op.responses) return;
          op.tags = [groupName];
        });
        keep[p] = item;
      });
      copy.paths = keep;
      copy.tags = [{ name: groupName }];
      if (copy.info) {
        copy.info.description = String(copy.info.description || '').split('\\n\\nGenerator notes:')[0];
      }
      return copy;
    }

    async function renderService(id, groupId, query) {
      const svc = (catalog.services || []).find(function(s) { return s.id === id; });
      if (!svc) { location.hash = 'overview'; return; }
      overviewLink.classList.remove('active');
      const groups = groupedOps(svc, query);
      const group = groupId ? groups[groupId] : null;
      crumb.innerHTML = group
        ? svc.title + ' <span>/ ' + group.name + '</span>'
        : svc.title;
      pills.innerHTML = '';
      if (svc.auth) {
        const p = document.createElement('span');
        p.className = 'pill auth';
        p.textContent = svc.auth;
        pills.appendChild(p);
      }
      if (svc.servers && svc.servers[0]) {
        const p = document.createElement('span');
        p.className = 'pill';
        p.textContent = svc.servers[0];
        pills.appendChild(p);
      }
      if (!group) {
        destroyUi();
        const tags = Object.keys(groups);
        let body = '';
        tags.sort(function(a, b) { return groups[a].name.localeCompare(groups[b].name); }).forEach(function(tag) {
          body += '<a class="tag-h" href="#' + encodeURIComponent(svc.id) + '/' + encodeURIComponent(tag) + '"></a>';
          groups[tag].ops.forEach(function(op) { body += opRow(svc, op); });
        });
        content.innerHTML = '<div class="hero"><h2></h2><p>Sub-projects for this repo. Pick one on the left to try it out.</p></div>' +
          '<section class="project"><div class="ops">' + body + '</div></section>';
        content.querySelector('h2').textContent = svc.title;
        const heads = content.querySelectorAll('.tag-h');
        tags.sort(function(a, b) { return groups[a].name.localeCompare(groups[b].name); }).forEach(function(tag, i) {
          heads[i].textContent = groups[tag].name;
          const rows = [];
          let node = heads[i].nextElementSibling;
          while (node && node.classList.contains('op')) { rows.push(node); node = node.nextElementSibling; }
          groups[tag].ops.forEach(function(op, j) {
            if (!rows[j]) return;
            rows[j].querySelector('.path').textContent = op.path;
            rows[j].querySelector('.sum').textContent = op.summary || '';
          });
        });
        return;
      }
      let spec = svc.spec || null;
      if (!spec && svc.spec_file) {
        try {
          const res = await fetch(svc.spec_file);
          if (res.ok) spec = await res.json();
        } catch (e) { spec = null; }
      }
      if (!spec) {
        const href = svc.html && /^[A-Za-z0-9._-]+\\.html$/.test(svc.html) ? svc.html : '';
        content.innerHTML = '<div class="hero"><h2></h2><p>Open the project page to try the grouped API list.</p>' +
          (href ? '<p><a href="' + href + '">Open ' + '</a></p>' : '') + '</div>';
        content.querySelector('h2').textContent = svc.title;
        if (href) content.querySelector('a').textContent = svc.title;
        return;
      }
      const paths = {};
      group.ops.forEach(function(op) { paths[op.path] = true; });
      mountSwagger(filterSpec(spec, group.name, paths), svc.auth_prefill || {});
    }

    function currentRoute() {
      const raw = (location.hash || '#overview').replace(/^#/, '');
      const h = decodeURIComponent(raw.replace(/^\//, ''));
      const bits = h.split('/').filter(Boolean);
      const first = (bits[0] || '').trim();
      if (!first || first === 'overview') return { svc: '', group: '' };
      if (!(catalog.services || []).some(function(s) { return s.id === first; })) {
        return { svc: '', group: '' };
      }
      return { svc: first, group: (bits[1] || '').trim() };
    }

    async function route() {
      const query = (q.value || '').trim().toLowerCase();
      const where = currentRoute();
      renderNav(query);
      closeMenu();
      if (!where.svc) {
        destroyUi();
        renderOverview(query);
        return;
      }
      await renderService(where.svc, where.group, query);
    }

    q.addEventListener('input', function() { route(); });
    window.addEventListener('hashchange', function() { route(); });
    document.addEventListener('keydown', function(ev) {
      if (ev.key === '/' && document.activeElement !== q) {
        ev.preventDefault();
        q.focus();
      }
    });
    route();
  </script>
</body>
</html>
"""


def catalog_from_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    services = []
    total = 0
    for entry in entries:
        ops = entry.get("operations") or []
        total += int(entry.get("endpoint_count") or len(ops) or 0)
        html_name = entry.get("html") or ""
        if html_name and not _SAFE_HREF.fullmatch(str(html_name)):
            html_name = ""
        spec_file = entry.get("spec_file") or ""
        if spec_file and not str(spec_file).endswith(".json"):
            spec_file = ""
        item = {
            "id": entry.get("id") or _slug(entry.get("name") or entry.get("title") or "svc"),
            "name": entry.get("name") or "",
            "title": entry.get("title") or entry.get("name") or "service",
            "version": entry.get("version") or "1.0.0",
            "endpoint_count": int(entry.get("endpoint_count") or len(ops) or 0),
            "frameworks": list(entry.get("frameworks") or []),
            "html": html_name,
            "spec_file": spec_file,
            "servers": list(entry.get("servers") or []),
            "auth": entry.get("auth") or "",
            "operations": ops,
        }
        if isinstance(entry.get("spec"), dict):
            item["spec"] = entry["spec"]
        services.append(item)
    return {"services": services, "endpoint_count": total}


def operations_for_spec(spec) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ep in spec.endpoints:
        group = subproject_name(ep.path, ep.tags, spec.title or spec.name)
        method = (ep.method or "get").upper()
        rows.append(
            {
                "method": method,
                "path": ep.path,
                "summary": ep.summary or ep.operation_id or "",
                "tag": group,
                "group": group,
                "groupId": _slug(group),
                "operationId": ep.operation_id or f"{ep.method}_{ep.path}",
            }
        )
    rows.sort(
        key=lambda r: (
            r["group"],
            _METHOD_ORDER.index(r["method"].lower())
            if r["method"].lower() in _METHOD_ORDER
            else 99,
            r["path"],
        )
    )
    return rows


def write_portal(out_dir, entries: list[dict[str, Any]]):
    """Write index.html — a project-grouped API explorer."""
    from pathlib import Path

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog = catalog_from_entries(entries)
    page = (
        _PORTAL_TEMPLATE.replace("__VER__", SWAGGER_UI_VERSION)
        .replace("__CATALOG_JSON__", _spec_json(catalog))
        .replace("__PROXY_BOOTSTRAP__", PROXY_BOOTSTRAP_OFF)
    )
    path = out_dir / "index.html"
    path.write_text(page, encoding="utf-8")
    return path
