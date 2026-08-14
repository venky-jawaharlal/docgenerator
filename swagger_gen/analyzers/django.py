"""Django / Django REST Framework analyzer.

Django does not attach HTTP verbs to URLconf entries, so this analyzer extracts
routes from ``urls.py`` (``path()`` / ``re_path()`` / ``url()``) and records the
view name. When it cannot tell the verb it documents the route under ``GET`` and
adds a note. This is intentionally best-effort - Django's routing is very
dynamic - but it still surfaces the full URL surface of the project.
"""

from __future__ import annotations

import re

from ..models import IN_PATH, ApiSpec, Endpoint, Parameter, Response
from ._common import humanize, path_param_names, to_openapi_path
from .base import BaseAnalyzer, RepoContext

_ROUTE_RE = re.compile(
    r"""(?:^|\s|\()(?:re_)?(?:path|url)\(\s*
        (?P<q>['"])(?P<route>.*?)(?P=q)\s*,\s*
        (?P<view>[^,\)]+)""",
    re.VERBOSE,
)


class DjangoAnalyzer(BaseAnalyzer):
    name = "django"
    display_name = "Django (Python)"

    def detect(self, ctx: RepoContext) -> bool:
        if ctx.has_dependency("django", "djangorestframework"):
            return True
        if ctx.files_named("manage.py"):
            return True
        return bool(ctx.files_named("urls.py"))

    def analyze(self, ctx: RepoContext, spec: ApiSpec) -> None:
        url_files = ctx.files_named("urls.py")
        if not url_files:
            spec.add_note("Django detected but no urls.py found to extract routes.")
            return
        ambiguous_verbs = False
        for path in url_files:
            text = ctx.read(path)
            rel = ctx.rel(path)
            for match in _ROUTE_RE.finditer(text):
                route = match.group("route")
                if route is None:
                    continue
                view = match.group("view").strip()
                open_path = to_openapi_path(route)
                if not open_path.startswith("/"):
                    open_path = "/" + open_path
                view_label = view.split(".")[-1].replace("()", "")
                ep = Endpoint(
                    path=open_path,
                    method="get",
                    operation_id=view_label or "view",
                    summary=humanize(view_label),
                    description=f"Django route mapped to view '{view}'.",
                    tags=[rel.split("/")[0] if "/" in rel else "django"],
                    source_file=rel,
                    source_line=text[: match.start()].count("\n") + 1,
                )
                for pname in path_param_names(open_path):
                    ep.parameters.append(
                        Parameter(name=pname, location=IN_PATH, required=True)
                    )
                ep.responses.append(Response())
                spec.add_endpoint(ep)
                ambiguous_verbs = True
        if ambiguous_verbs:
            spec.add_note(
                "Django routes have no explicit HTTP verb; documented as GET. "
                "Review view classes for the true allowed methods."
            )
