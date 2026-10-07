"""FastAPI analyzer.

Looks for path-operation decorators (``@app.get``, ``@router.post``, ...),
extracts the route path and infers parameters from the handler signature:
path params match ``{name}`` placeholders, arguments with a Pydantic-model
annotation become the request body, ``Depends``/``Security`` and OAuth2/HTTP
bearer dependencies surface as security schemes.
"""

from __future__ import annotations

import ast

from ..models import (
    IN_PATH,
    IN_QUERY,
    ApiSpec,
    Endpoint,
    Parameter,
    RequestBody,
    Response,
    Schema,
    SecurityScheme,
)
from ._common import humanize, parse_python, path_param_names, to_openapi_path
from .base import BaseAnalyzer, RepoContext

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
SCALARS = {"int", "float", "str", "bool", "bytes", "UUID"}
SCALAR_TO_OPENAPI = {
    "int": "integer",
    "float": "number",
    "str": "string",
    "bool": "boolean",
    "bytes": "string",
    "UUID": "string",
}


class FastAPIAnalyzer(BaseAnalyzer):
    name = "fastapi"
    display_name = "FastAPI (Python)"

    def detect(self, ctx: RepoContext) -> bool:
        if ctx.has_dependency("fastapi"):
            return True
        for path in ctx.files_with_ext(".py"):
            text = ctx.read(path)
            if "fastapi" in text and ("FastAPI(" in text or "APIRouter(" in text):
                return True
        return False

    def analyze(self, ctx: RepoContext, spec: ApiSpec) -> None:
        bearer_seen = False
        for path in ctx.files_with_ext(".py"):
            text = ctx.read(path)
            if "fastapi" not in text and "APIRouter" not in text:
                continue
            try:
                tree = parse_python(text, str(path))
            except SyntaxError:
                continue
            rel = ctx.rel(path)
            prefixes = self._router_prefixes(tree)
            if any(tok in text for tok in ("OAuth2PasswordBearer", "HTTPBearer")):
                bearer_seen = True
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._handle_operation(node, text, rel, prefixes, spec)

        if bearer_seen:
            spec.add_security_scheme(
                SecurityScheme(
                    name="bearerAuth",
                    type="http",
                    scheme="bearer",
                    bearer_format="JWT",
                    description="OAuth2/JWT bearer token.",
                )
            )
            spec.add_note("Token-based authentication detected (OAuth2/HTTP bearer).")

    @staticmethod
    def _router_prefixes(tree) -> dict[str, str]:
        """Map router/app variable name -> prefix from APIRouter(prefix=...)."""
        prefixes: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call = node.value
                fn = call.func
                fname = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                if fname in ("APIRouter", "FastAPI"):
                    prefix = ""
                    for kw in call.keywords:
                        if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                            prefix = str(kw.value.value)
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            prefixes[tgt.id] = prefix
        return prefixes

    def _handle_operation(self, node, text, rel, prefixes, spec: ApiSpec) -> None:
        for deco in node.decorator_list:
            parsed = self._parse_operation_decorator(deco, prefixes)
            if parsed is None:
                continue
            method, raw_path, tags = parsed
            open_path = to_openapi_path(raw_path)
            ep = Endpoint(
                path=open_path,
                method=method,
                operation_id=node.name,
                summary=humanize(node.name),
                description=ast.get_docstring(node) or "",
                tags=tags or [rel.split("/")[0] if "/" in rel else rel],
                source_file=rel,
                source_line=node.lineno,
            )
            self._params_from_signature(node, open_path, ep, spec)
            if not ep.responses:
                ep.responses.append(Response())
            spec.add_endpoint(ep)

    def _parse_operation_decorator(self, deco, prefixes):
        if not isinstance(deco, ast.Call):
            return None
        func = deco.func
        if not isinstance(func, ast.Attribute) or func.attr not in HTTP_METHODS:
            return None
        if not deco.args or not isinstance(deco.args[0], ast.Constant):
            return None
        owner = func.value.id if isinstance(func.value, ast.Name) else ""
        prefix = prefixes.get(owner, "")
        raw_path = prefix + str(deco.args[0].value)
        tags: list[str] = []
        for kw in deco.keywords:
            if kw.arg == "tags" and isinstance(kw.value, (ast.List, ast.Tuple)):
                tags = [
                    str(e.value) for e in kw.value.elts if isinstance(e, ast.Constant)
                ]
        return func.attr, raw_path, tags

    def _params_from_signature(self, node, open_path, ep: Endpoint, spec: ApiSpec):
        path_names = set(path_param_names(open_path))
        args = node.args
        defaults = args.defaults
        pos = args.args
        default_offset = len(pos) - len(defaults)
        for idx, arg in enumerate(pos):
            aname = arg.arg
            if aname in ("self", "cls", "request", "response"):
                continue
            ann = self._annotation_name(arg.annotation)
            default = None
            if idx >= default_offset:
                default = defaults[idx - default_offset]

            if self._is_dependency(default):
                if self._is_security_dep(default):
                    ep.security.append("bearerAuth")
                continue

            if aname in path_names:
                ep.parameters.append(
                    Parameter(
                        name=aname,
                        location=IN_PATH,
                        required=True,
                        schema_type=SCALAR_TO_OPENAPI.get(ann, "string"),
                    )
                )
            elif ann and ann not in SCALARS and ann not in ("dict", "list", "Any"):
                # Non-scalar annotation => Pydantic model => request body.
                if ep.method in ("post", "put", "patch"):
                    ep.request_body = RequestBody(
                        schema_type="object",
                        description=f"Request body ({ann}).",
                        schema_ref=ann,
                    )
                    spec.schemas.setdefault(ann, Schema(name=ann))
            else:
                required = default is None
                ep.parameters.append(
                    Parameter(
                        name=aname,
                        location=IN_QUERY,
                        required=required,
                        schema_type=SCALAR_TO_OPENAPI.get(ann, "string"),
                    )
                )

    @staticmethod
    def _annotation_name(annotation) -> str:
        if annotation is None:
            return ""
        if isinstance(annotation, ast.Name):
            return annotation.id
        if isinstance(annotation, ast.Attribute):
            return annotation.attr
        if isinstance(annotation, ast.Subscript):
            # e.g. Optional[Model], List[Item] -> take the inner-most Name.
            return FastAPIAnalyzer._annotation_name(annotation.slice)
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            return annotation.value
        return ""

    @staticmethod
    def _is_dependency(default) -> bool:
        if not isinstance(default, ast.Call):
            return False
        fn = default.func
        fname = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        return fname in ("Depends", "Security")

    @staticmethod
    def _is_security_dep(default) -> bool:
        if not isinstance(default, ast.Call):
            return False
        fn = default.func
        fname = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        return fname == "Security"
