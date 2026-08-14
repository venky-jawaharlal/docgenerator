"""JAX-RS (Jersey / RESTEasy / Java EE) analyzer.

Extracts resources annotated with ``@Path`` and methods annotated with
``@GET`` / ``@POST`` / ``@PUT`` / ``@DELETE`` / ``@PATCH``, plus ``@PathParam``,
``@QueryParam`` and ``@HeaderParam`` arguments.
"""

from __future__ import annotations

import re

from ..models import (
    IN_HEADER,
    IN_PATH,
    IN_QUERY,
    ApiSpec,
    Endpoint,
    Parameter,
    RequestBody,
    Response,
)
from ._common import humanize, join_paths, to_openapi_path
from .base import BaseAnalyzer, RepoContext
from .spring import _balanced_parens, _java_type_to_openapi, _split_params

_HTTP_ANNO_RE = re.compile(r"@(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b")


class JaxRsAnalyzer(BaseAnalyzer):
    name = "jaxrs"
    display_name = "JAX-RS (Java)"

    def detect(self, ctx: RepoContext) -> bool:
        if ctx.has_dependency("jersey", "resteasy", "javax.ws.rs", "jakarta.ws.rs"):
            return True
        for path in ctx.files_with_ext(".java"):
            text = ctx.read(path)
            if "javax.ws.rs" in text or "jakarta.ws.rs" in text:
                return True
        return False

    def analyze(self, ctx: RepoContext, spec: ApiSpec) -> None:
        for path in ctx.files_with_ext(".java"):
            text = ctx.read(path)
            if "ws.rs" not in text and not _HTTP_ANNO_RE.search(text):
                continue
            rel = ctx.rel(path)
            base_path = self._class_path(text)
            tag = self._class_name(text) or rel
            for match in _HTTP_ANNO_RE.finditer(text):
                verb = match.group(1)
                sub_path, method_name, params = self._read_after(text, match.end())
                if method_name is None:
                    continue
                full_path = to_openapi_path(join_paths(base_path, sub_path))
                ep = Endpoint(
                    path=full_path,
                    method=verb.lower(),
                    operation_id=method_name,
                    summary=humanize(method_name),
                    tags=[tag],
                    source_file=rel,
                    source_line=text[: match.start()].count("\n") + 1,
                )
                self._apply_params(params, ep)
                ep.responses.append(Response())
                spec.add_endpoint(ep)

    @staticmethod
    def _class_name(text: str) -> str:
        m = re.search(r"\bclass\s+(\w+)", text)
        return m.group(1) if m else ""

    @staticmethod
    def _class_path(text: str) -> str:
        idx = re.search(r"\bclass\b", text)
        header = text[: idx.start()] if idx else text
        m = re.search(r"@Path\s*\(\s*\"([^\"]*)\"", header)
        return m.group(1) if m else ""

    @staticmethod
    def _read_after(text: str, pos: int):
        """From just after an HTTP verb annotation, read optional @Path, then sig."""
        sub_path = ""
        window = text[pos:pos + 400]
        m = re.search(r"@Path\s*\(\s*\"([^\"]*)\"", window)
        if m:
            sub_path = m.group(1)
        paren = text.find("(", pos)
        if paren == -1:
            return sub_path, None, ""
        head = text[pos:paren]
        names = re.findall(r"[A-Za-z_]\w*", head)
        method_name = names[-1] if names else None
        params, _ = _balanced_parens(text, paren)
        return sub_path, method_name, params

    def _apply_params(self, params_str: str, ep: Endpoint) -> None:
        for raw in _split_params(params_str):
            anno = re.search(
                r"@(PathParam|QueryParam|HeaderParam)\s*\(\s*\"([^\"]*)\"\s*\)", raw
            )
            idents = re.findall(r"[A-Za-z_]\w*", re.sub(r"@\w+(\([^)]*\))?", "", raw))
            type_token = idents[-2] if len(idents) >= 2 else "String"
            if not anno:
                # Unannotated non-primitive => likely request entity (body).
                if type_token.lower() not in ("string", "int", "long", "boolean"):
                    if ep.method in ("post", "put", "patch"):
                        ep.request_body = RequestBody(schema_ref=type_token)
                continue
            kind, name = anno.group(1), anno.group(2)
            location = {
                "PathParam": IN_PATH,
                "QueryParam": IN_QUERY,
                "HeaderParam": IN_HEADER,
            }[kind]
            ep.parameters.append(
                Parameter(
                    name=name,
                    location=location,
                    required=location == IN_PATH,
                    schema_type=_java_type_to_openapi(type_token),
                )
            )
