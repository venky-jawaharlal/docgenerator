"""Framework-agnostic intermediate representation (IR).

Every analyzer produces these objects regardless of the source tech stack. The
OpenAPI builder consumes them, so adding a new framework only means emitting the
IR - the rest of the pipeline is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Locations an OpenAPI parameter can live in.
IN_PATH = "path"
IN_QUERY = "query"
IN_HEADER = "header"
IN_COOKIE = "cookie"


@dataclass
class Parameter:
    name: str
    location: str = IN_QUERY
    required: bool = False
    schema_type: str = "string"
    description: str = ""


@dataclass
class RequestBody:
    schema_type: str = "object"
    content_type: str = "application/json"
    description: str = ""
    required: bool = True
    # Optional reference to a named schema in components/schemas.
    schema_ref: Optional[str] = None


@dataclass
class Response:
    status_code: str = "200"
    description: str = "Successful response"
    content_type: str = "application/json"
    schema_type: str = "object"
    schema_ref: Optional[str] = None


@dataclass
class Server:
    """An OpenAPI server entry — the host Try-it-out requests are sent to."""

    url: str
    description: str = ""


@dataclass
class Endpoint:
    path: str
    method: str  # lower-case http verb: get/post/put/delete/patch...
    operation_id: str = ""
    summary: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    request_body: Optional[RequestBody] = None
    responses: list[Response] = field(default_factory=list)
    # Names of security schemes (keys in ApiSpec.security_schemes) that guard
    # this endpoint. Empty means "inherits the spec-level default".
    security: list[str] = field(default_factory=list)
    deprecated: bool = False
    source_file: str = ""
    source_line: int = 0
    # Per-operation servers (used in combined docs so each service hits its host).
    servers: list[Server] = field(default_factory=list)

    def key(self) -> tuple[str, str]:
        return (self.path, self.method.lower())


@dataclass
class SecurityScheme:
    name: str
    type: str  # "http" | "apiKey" | "oauth2" | "openIdConnect"
    scheme: str = ""  # for http: "bearer" | "basic"
    bearer_format: str = ""  # e.g. "JWT"
    location: str = ""  # for apiKey: header | query | cookie
    param_name: str = ""  # for apiKey: the header/cookie/query name
    description: str = ""
    flows: dict = field(default_factory=dict)  # for oauth2


@dataclass
class Schema:
    """A named component schema (best-effort; often a bare object)."""

    name: str
    properties: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class ApiSpec:
    """Everything discovered for a single repository."""

    name: str
    title: str
    version: str = "1.0.0"
    description: str = ""
    base_path: str = ""
    servers: list[Server] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)
    security_schemes: dict[str, SecurityScheme] = field(default_factory=dict)
    schemas: dict[str, Schema] = field(default_factory=dict)
    default_security: list[str] = field(default_factory=list)
    detected_frameworks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tag_meta: dict[str, str] = field(default_factory=dict)

    def add_endpoint(self, endpoint: Endpoint) -> None:
        """Add an endpoint, skipping exact (path, method) duplicates."""
        existing = {e.key() for e in self.endpoints}
        if endpoint.key() not in existing:
            self.endpoints.append(endpoint)

    def add_security_scheme(self, scheme: SecurityScheme) -> None:
        self.security_schemes.setdefault(scheme.name, scheme)

    def add_note(self, note: str) -> None:
        if note and note not in self.notes:
            self.notes.append(note)
