"""Existing OpenAPI/Swagger importer.

If a repo already ships an ``openapi.(yaml|json)`` / ``swagger.(yaml|json)``
document, that is by far the most authoritative source. This analyzer parses it
into the shared IR so it merges cleanly with (or replaces) anything the
heuristic analyzers find for the same repo.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from ..models import (
    ApiSpec,
    Endpoint,
    Parameter,
    RequestBody,
    Response,
    SecurityScheme,
)
from .base import BaseAnalyzer, RepoContext

_HTTP_VERBS = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}
_SPEC_STEMS = ("openapi", "swagger", "api", "api-docs", "apidocs")


class ExistingSpecAnalyzer(BaseAnalyzer):
    name = "openapi"
    display_name = "Existing OpenAPI/Swagger spec"

    def detect(self, ctx: RepoContext) -> bool:
        return bool(self._spec_files(ctx))

    def analyze(self, ctx: RepoContext, spec: ApiSpec) -> None:
        for path in self._spec_files(ctx):
            data = self._load(path)
            if not isinstance(data, dict) or "paths" not in data:
                continue
            spec.add_note(f"Imported existing API spec: {ctx.rel(path)}")
            self._merge(data, spec)

    @staticmethod
    def _spec_files(ctx: RepoContext) -> list[Path]:
        results: list[Path] = []
        for path in ctx.files_with_ext(".json", ".yaml", ".yml"):
            stem = path.stem.lower()
            if any(stem == s or stem.startswith(s) for s in _SPEC_STEMS):
                results.append(path)
        return results

    @staticmethod
    def _load(path: Path):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if path.suffix.lower() == ".json":
                return json.loads(text)
            return yaml.safe_load(text)
        except (OSError, ValueError, yaml.YAMLError):
            return None

    def _merge(self, data: dict, spec: ApiSpec) -> None:
        info = data.get("info", {}) or {}
        if info.get("title") and spec.title == spec.name:
            spec.title = info["title"]
        if info.get("version"):
            spec.version = info["version"]
        if info.get("description") and not spec.description:
            spec.description = info["description"]

        for name, scheme in (
            (data.get("components", {}) or {}).get("securitySchemes", {}) or {}
        ).items():
            spec.add_security_scheme(
                SecurityScheme(
                    name=name,
                    type=scheme.get("type", "http"),
                    scheme=scheme.get("scheme", ""),
                    bearer_format=scheme.get("bearerFormat", ""),
                    location=scheme.get("in", ""),
                    param_name=scheme.get("name", ""),
                    description=scheme.get("description", ""),
                    flows=scheme.get("flows", {}) or {},
                )
            )

        for route, methods in (data.get("paths", {}) or {}).items():
            if not isinstance(methods, dict):
                continue
            for verb, op in methods.items():
                if verb.lower() not in _HTTP_VERBS or not isinstance(op, dict):
                    continue
                ep = Endpoint(
                    path=route,
                    method=verb.lower(),
                    operation_id=op.get("operationId", ""),
                    summary=op.get("summary", ""),
                    description=op.get("description", ""),
                    tags=list(op.get("tags", []) or []),
                    deprecated=bool(op.get("deprecated", False)),
                )
                for param in op.get("parameters", []) or []:
                    if not isinstance(param, dict):
                        continue
                    ep.parameters.append(
                        Parameter(
                            name=param.get("name", ""),
                            location=param.get("in", "query"),
                            required=bool(param.get("required", False)),
                            schema_type=(param.get("schema", {}) or {}).get(
                                "type", "string"
                            ),
                            description=param.get("description", ""),
                        )
                    )
                if "requestBody" in op:
                    ep.request_body = RequestBody(
                        description=(op["requestBody"] or {}).get("description", ""),
                        required=bool((op["requestBody"] or {}).get("required", True)),
                    )
                for code, resp in (op.get("responses", {}) or {}).items():
                    ep.responses.append(
                        Response(
                            status_code=str(code),
                            description=(resp or {}).get("description", ""),
                        )
                    )
                if not ep.responses:
                    ep.responses.append(Response())
                for sec in op.get("security", []) or []:
                    ep.security.extend(sec.keys())
                spec.add_endpoint(ep)
