"""Convert the internal IR (:class:`ApiSpec`) into an OpenAPI 3.0.3 document."""

from __future__ import annotations

from typing import Any

from .models import ApiSpec, Endpoint, Parameter, SecurityScheme
from .servers import to_openapi as servers_to_openapi
from .tags import operation_tags


def _param_to_openapi(param: Parameter) -> dict[str, Any]:
    node: dict[str, Any] = {
        "name": param.name,
        "in": param.location,
        "required": True if param.location == "path" else param.required,
        "schema": {"type": param.schema_type or "string"},
    }
    if param.description:
        node["description"] = param.description
    return node


def _scheme_to_openapi(scheme: SecurityScheme) -> dict[str, Any]:
    node: dict[str, Any] = {"type": scheme.type}
    if scheme.description:
        node["description"] = scheme.description
    if scheme.type == "http":
        node["scheme"] = scheme.scheme or "bearer"
        if scheme.bearer_format:
            node["bearerFormat"] = scheme.bearer_format
    elif scheme.type == "apiKey":
        node["in"] = scheme.location or "header"
        node["name"] = scheme.param_name or "X-API-Key"
    elif scheme.type == "oauth2":
        node["flows"] = scheme.flows or {}
    return node


def _operation(ep: Endpoint, project: str | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {
        "operationId": ep.operation_id or f"{ep.method}_{ep.path}",
        "responses": {},
    }
    if ep.summary:
        op["summary"] = ep.summary
    if ep.description:
        op["description"] = ep.description
    tags = operation_tags(ep.tags, project=project)
    op["tags"] = tags
    if ep.deprecated:
        op["deprecated"] = True

    parameters = [_param_to_openapi(p) for p in ep.parameters if p.name]
    if parameters:
        op["parameters"] = parameters

    if ep.request_body is not None:
        rb = ep.request_body
        schema: dict[str, Any] = (
            {"$ref": f"#/components/schemas/{rb.schema_ref}"}
            if rb.schema_ref
            else {"type": rb.schema_type or "object"}
        )
        op["requestBody"] = {
            "required": rb.required,
            "content": {rb.content_type: {"schema": schema}},
        }
        if rb.description:
            op["requestBody"]["description"] = rb.description

    responses = ep.responses or []
    if not responses:
        op["responses"]["200"] = {"description": "Successful response"}
    for resp in responses:
        node: dict[str, Any] = {"description": resp.description or "Response"}
        if resp.content_type and (resp.schema_ref or resp.schema_type):
            schema = (
                {"$ref": f"#/components/schemas/{resp.schema_ref}"}
                if resp.schema_ref
                else {"type": resp.schema_type or "object"}
            )
            node["content"] = {resp.content_type: {"schema": schema}}
        op["responses"][resp.status_code] = node

    if ep.security:
        op["security"] = [{name: []} for name in dict.fromkeys(ep.security)]
    return op


def _referenced_schema_names(spec: ApiSpec) -> set[str]:
    refs: set[str] = set()
    for ep in spec.endpoints:
        if ep.request_body and ep.request_body.schema_ref:
            refs.add(ep.request_body.schema_ref)
        for resp in ep.responses:
            if resp.schema_ref:
                refs.add(resp.schema_ref)
    return refs


def build_openapi(spec: ApiSpec) -> dict[str, Any]:
    """Return a fully-formed OpenAPI 3.0.3 mapping for ``spec``."""
    description = spec.description or ""
    if spec.detected_frameworks:
        fw = ", ".join(spec.detected_frameworks)
        description = (description + f"\n\nDetected stack: {fw}.").strip()
    if spec.notes:
        note_block = "\n".join(f"- {n}" for n in spec.notes)
        description = (description + "\n\nGenerator notes:\n" + note_block).strip()

    doc: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": spec.title or spec.name,
            "version": spec.version or "1.0.0",
            "description": description,
        },
    }

    if spec.servers:
        doc["servers"] = servers_to_openapi(spec.servers)
    elif spec.base_path and spec.base_path.startswith(("http://", "https://")):
        doc["servers"] = [{"url": spec.base_path}]

    if spec.default_security:
        doc["security"] = [{name: []} for name in dict.fromkeys(spec.default_security)]

    paths: dict[str, Any] = {}
    for ep in sorted(spec.endpoints, key=lambda e: (e.path, e.method)):
        item = paths.setdefault(ep.path, {})
        if ep.servers and "servers" not in item:
            item["servers"] = servers_to_openapi(ep.servers)
        item[ep.method.lower()] = _operation(ep, project=spec.title or spec.name)
    doc["paths"] = paths

    components: dict[str, Any] = {}
    if spec.security_schemes:
        components["securitySchemes"] = {
            name: _scheme_to_openapi(scheme)
            for name, scheme in spec.security_schemes.items()
        }

    schemas: dict[str, Any] = {
        name: {
            "type": "object",
            **({"description": s.description} if s.description else {}),
            **({"properties": s.properties} if s.properties else {}),
        }
        for name, s in spec.schemas.items()
    }
    # Guarantee every $ref used by an operation resolves to a defined schema so
    # the document validates even when the model body could not be parsed.
    for ref in _referenced_schema_names(spec):
        schemas.setdefault(
            ref,
            {
                "type": "object",
                "description": (
                    "Schema inferred from source; properties were not resolved "
                    "statically."
                ),
            },
        )
    if schemas:
        components["schemas"] = schemas
    if components:
        doc["components"] = components

    tag_nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ep in spec.endpoints:
        for name in operation_tags(ep.tags, project=spec.title or spec.name):
            if name in seen:
                continue
            seen.add(name)
            node: dict[str, Any] = {"name": name}
            desc = spec.tag_meta.get(name, "")
            if desc:
                node["description"] = desc
            tag_nodes.append(node)
    if tag_nodes:
        doc["tags"] = tag_nodes

    return doc
