"""Flask analyzer.

Parses Python source with the ``ast`` module and looks for view functions
decorated with ``@app.route`` / ``@blueprint.route`` (and the ``@app.get`` /
``@app.post`` shortcuts added in Flask 2.0). Path params, query usage, JSON
bodies and session/login-based auth are inferred heuristically.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ..models import (
    IN_COOKIE,
    IN_PATH,
    IN_QUERY,
    ApiSpec,
    Endpoint,
    Parameter,
    RequestBody,
    Response,
    SecurityScheme,
)
from ._common import humanize, path_param_names, to_openapi_path
from .base import BaseAnalyzer, RepoContext

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


class FlaskAnalyzer(BaseAnalyzer):
    name = "flask"
    display_name = "Flask (Python)"

    def detect(self, ctx: RepoContext) -> bool:
        if ctx.has_dependency("flask"):
            return True
        for path in ctx.files_with_ext(".py"):
            text = ctx.read(path)
            if "from flask" in text or "import flask" in text:
                if ".route(" in text or "Flask(" in text or "Blueprint(" in text:
                    return True
        return False

    def analyze(self, ctx: RepoContext, spec: ApiSpec) -> None:
        found_session = False
        for path in ctx.files_with_ext(".py"):
            text = ctx.read(path)
            if ".route(" not in text and not any(
                f".{m}(" in text for m in HTTP_METHODS
            ):
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            rel = ctx.rel(path)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found_session |= self._handle_view(node, text, rel, spec)

        if found_session:
            spec.add_security_scheme(
                SecurityScheme(
                    name="sessionCookie",
                    type="apiKey",
                    location=IN_COOKIE,
                    param_name="session",
                    description="Flask signed session cookie.",
                )
            )
            spec.add_note("Session-based authentication detected (Flask session).")

    def _handle_view(self, node, text: str, rel: str, spec: ApiSpec) -> bool:
        uses_session = False
        for deco in node.decorator_list:
            route = self._parse_route_decorator(deco)
            if route is None:
                continue
            raw_path, methods = route
            open_path = to_openapi_path(raw_path)
            body_src = ast.get_source_segment(text, node) or ""
            uses_session = uses_session or self._uses_session(body_src)

            for method in methods:
                ep = Endpoint(
                    path=open_path,
                    method=method.lower(),
                    operation_id=f"{node.name}_{method.lower()}",
                    summary=humanize(node.name),
                    description=ast.get_docstring(node) or "",
                    tags=[rel.split("/")[0] if "/" in rel else rel],
                    source_file=rel,
                    source_line=node.lineno,
                )
                for pname in path_param_names(open_path):
                    ep.parameters.append(
                        Parameter(name=pname, location=IN_PATH, required=True)
                    )
                self._add_query_params(body_src, ep)
                if method.lower() in ("post", "put", "patch") and (
                    "get_json" in body_src or "request.json" in body_src
                ):
                    ep.request_body = RequestBody(
                        description="JSON request body (inferred from request.get_json)."
                    )
                ep.responses.append(Response())
                if self._requires_auth(node, body_src):
                    ep.security.append("sessionCookie")
                spec.add_endpoint(ep)
        return uses_session

    @staticmethod
    def _parse_route_decorator(deco) -> tuple[str, list[str]] | None:
        if not isinstance(deco, ast.Call):
            return None
        func = deco.func
        if not isinstance(func, ast.Attribute):
            return None
        attr = func.attr
        # @app.route("/x", methods=["GET"])
        if attr == "route":
            if not deco.args or not isinstance(deco.args[0], ast.Constant):
                return None
            path = str(deco.args[0].value)
            methods = ["GET"]
            for kw in deco.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods = [
                        str(e.value)
                        for e in kw.value.elts
                        if isinstance(e, ast.Constant)
                    ] or methods
            return path, methods
        # @app.get("/x") / @bp.post("/x")
        if attr in HTTP_METHODS:
            if deco.args and isinstance(deco.args[0], ast.Constant):
                return str(deco.args[0].value), [attr.upper()]
        return None

    @staticmethod
    def _add_query_params(body_src: str, ep: Endpoint) -> None:
        import re

        seen = {p.name for p in ep.parameters}
        for match in re.finditer(
            r"request\.args(?:\.get)?\(\s*['\"]([^'\"]+)['\"]", body_src
        ):
            name = match.group(1)
            if name not in seen:
                ep.parameters.append(Parameter(name=name, location=IN_QUERY))
                seen.add(name)

    @staticmethod
    def _uses_session(body_src: str) -> bool:
        return "session[" in body_src or "session.get(" in body_src

    @staticmethod
    def _requires_auth(node, body_src: str) -> bool:
        deco_names = []
        for deco in node.decorator_list:
            target = deco.func if isinstance(deco, ast.Call) else deco
            if isinstance(target, ast.Name):
                deco_names.append(target.id)
            elif isinstance(target, ast.Attribute):
                deco_names.append(target.attr)
        if "login_required" in deco_names:
            return True
        return "session[" in body_src or "session.get(" in body_src
