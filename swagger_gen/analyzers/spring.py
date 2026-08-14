"""Spring Boot (Spring MVC / WebFlux) analyzer.

Java is parsed with targeted regex + a small balanced-parenthesis scanner
(rather than a full Java grammar) which is robust enough for the annotation
shapes REST controllers use in practice:

    @RestController
    @RequestMapping("/api/users")
    class UserController {
        @GetMapping("/{id}")
        @PreAuthorize("hasRole('ADMIN')")
        User get(@PathVariable Long id, @RequestParam(required=false) String q) { ... }
    }

Path/query/header params, request bodies, method-level and class-level security
(``@PreAuthorize`` / ``@Secured`` / ``@RolesAllowed``) and ``HttpSession`` usage
are all extracted.
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
    SecurityScheme,
)
from ._common import humanize, join_paths, to_openapi_path
from .base import BaseAnalyzer, RepoContext

_MAPPING_RE = re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b")
_SECURITY_ANNOS = ("PreAuthorize", "Secured", "RolesAllowed", "PostAuthorize")
_MODIFIERS = {
    "public", "protected", "private", "static", "final", "abstract",
    "synchronized", "default", "native", "strictfp",
}
_TYPE_KEYWORDS = {"class", "interface", "enum", "record"}

_JAVA_TYPE_MAP = {
    "long": "integer", "int": "integer", "integer": "integer", "short": "integer",
    "byte": "integer", "biginteger": "integer",
    "double": "number", "float": "number", "bigdecimal": "number",
    "boolean": "boolean",
}


def _balanced_parens(text: str, open_idx: int) -> tuple[str, int]:
    """Given index of '(', return (inner_content, index_after_matching_')')."""
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i], i + 1
    return text[open_idx + 1:], len(text)


def _extract_path(args: str) -> str:
    if not args:
        return ""
    m = re.search(r"(?:value|path)\s*=\s*(\{[^}]*\}|\"[^\"]*\")", args)
    token = m.group(1) if m else args
    q = re.search(r"\"([^\"]*)\"", token)
    return q.group(1) if q else ""


def _split_params(param_str: str) -> list[str]:
    """Split a Java parameter list on top-level commas (ignoring <>, (), {})."""
    parts, depth, buf = [], 0, []
    for ch in param_str:
        if ch in "<({[":
            depth += 1
        elif ch in ">)}]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _java_type_to_openapi(java_type: str) -> str:
    base = re.sub(r"<.*>", "", java_type).strip().lower()
    base = base.split(".")[-1]
    return _JAVA_TYPE_MAP.get(base, "string")


class SpringAnalyzer(BaseAnalyzer):
    name = "spring"
    display_name = "Spring Boot (Java)"

    def detect(self, ctx: RepoContext) -> bool:
        if ctx.has_dependency("spring-boot", "spring-web", "springframework"):
            return True
        for path in ctx.files_with_ext(".java"):
            text = ctx.read(path)
            if "@RestController" in text or "@Controller" in text:
                return True
        return False

    def analyze(self, ctx: RepoContext, spec: ApiSpec) -> None:
        uses_session = False
        uses_security = False
        for path in ctx.files_with_ext(".java"):
            text = ctx.read(path)
            if "@RestController" not in text and "@Controller" not in text:
                continue
            rel = ctx.rel(path)
            base_path, class_security = self._class_level(text)
            class_tag = self._class_name(text) or rel
            for ep, sess in self._methods(text, base_path, class_security, class_tag, rel):
                uses_session |= sess
                uses_security |= bool(ep.security)
                spec.add_endpoint(ep)

        if ctx.has_dependency("spring-security", "spring-boot-starter-security"):
            uses_security = True
        if uses_security:
            spec.add_security_scheme(
                SecurityScheme(
                    name="bearerAuth",
                    type="http",
                    scheme="bearer",
                    bearer_format="JWT",
                    description="Spring Security bearer token (JWT).",
                )
            )
            spec.add_note("Authorization detected via Spring Security annotations.")
        if uses_session:
            spec.add_security_scheme(
                SecurityScheme(
                    name="sessionCookie",
                    type="apiKey",
                    location="cookie",
                    param_name="JSESSIONID",
                    description="Servlet session cookie (HttpSession).",
                )
            )
            spec.add_note("Session-based state detected (HttpSession / JSESSIONID).")

    @staticmethod
    def _class_name(text: str) -> str:
        m = re.search(r"\b(?:class|interface)\s+(\w+)", text)
        return m.group(1) if m else ""

    def _class_level(self, text: str) -> tuple[str, list[str]]:
        """Return (base path, security scheme names) declared at class scope."""
        base_path = ""
        security: list[str] = []
        class_idx = re.search(r"\b(?:public\s+|final\s+|abstract\s+)*class\b", text)
        header = text[: class_idx.start()] if class_idx else text
        m = re.search(r"@RequestMapping\b", header)
        if m:
            after = m.end()
            paren = header.find("(", after)
            if paren != -1 and header[after:paren].strip() == "":
                args, _ = _balanced_parens(header, paren)
                base_path = _extract_path(args)
        if any(f"@{anno}" in header for anno in _SECURITY_ANNOS):
            security.append("bearerAuth")
        return base_path, security

    def _methods(self, text, base_path, class_security, class_tag, rel):
        for match in _MAPPING_RE.finditer(text):
            verb = match.group(1)
            anno_end = match.end()
            # Extract mapping annotation args (if any).
            args = ""
            probe = anno_end
            while probe < len(text) and text[probe] in " \t":
                probe += 1
            if probe < len(text) and text[probe] == "(":
                args, anno_end = _balanced_parens(text, probe)

            # Skip following annotations; collect method-level security.
            pos, method_security = self._skip_annotations(text, anno_end)
            # If we ran into a type declaration, this was class-level: skip.
            if self._is_type_decl_ahead(text, pos):
                continue
            sig = self._read_signature(text, pos)
            if sig is None:
                continue
            method_name, params_str = sig

            methods = self._verbs(verb, args)
            raw_path = _extract_path(args)
            full_path = to_openapi_path(join_paths(base_path, raw_path))
            security = list(dict.fromkeys(class_security + method_security))

            for http_verb in methods:
                ep = Endpoint(
                    path=full_path,
                    method=http_verb.lower(),
                    operation_id=f"{method_name}",
                    summary=humanize(method_name),
                    tags=[class_tag],
                    security=list(security),
                    source_file=rel,
                    source_line=text[: match.start()].count("\n") + 1,
                )
                sess = self._apply_params(params_str, ep)
                ep.responses.append(Response())
                yield ep, sess

    @staticmethod
    def _verbs(verb: str, args: str) -> list[str]:
        if verb != "Request":
            return [verb.upper()]
        found = re.findall(r"RequestMethod\.(\w+)", args)
        return [v.upper() for v in found] or ["GET"]

    def _skip_annotations(self, text: str, pos: int) -> tuple[int, list[str]]:
        """Advance past annotations preceding the method; return security names."""
        security: list[str] = []
        while True:
            while pos < len(text) and text[pos] in " \t\r\n":
                pos += 1
            if pos >= len(text) or text[pos] != "@":
                break
            m = re.match(r"@(\w+)", text[pos:])
            if not m:
                break
            anno = m.group(1)
            pos += m.end()
            while pos < len(text) and text[pos] in " \t":
                pos += 1
            if pos < len(text) and text[pos] == "(":
                _, pos = _balanced_parens(text, pos)
            if anno in _SECURITY_ANNOS:
                security.append("bearerAuth")
        return pos, security

    @staticmethod
    def _is_type_decl_ahead(text: str, pos: int) -> bool:
        window = text[pos:pos + 120]
        # Strip modifiers, then see if a type keyword comes before '('.
        tokens = re.findall(r"[A-Za-z_@<>\w]+|\(", window)
        for tok in tokens:
            if tok == "(":
                return False
            if tok in _MODIFIERS:
                continue
            if tok in _TYPE_KEYWORDS:
                return True
            return False
        return False

    @staticmethod
    def _read_signature(text: str, pos: int) -> tuple[str, str] | None:
        paren = text.find("(", pos)
        if paren == -1:
            return None
        head = text[pos:paren]
        # Method name is the last identifier before '('.
        names = re.findall(r"[A-Za-z_]\w*", head)
        if not names:
            return None
        method_name = names[-1]
        params_str, _ = _balanced_parens(text, paren)
        return method_name, params_str

    def _apply_params(self, params_str: str, ep: Endpoint) -> bool:
        uses_session = False
        for raw in _split_params(params_str):
            uses_session |= self._apply_one_param(raw, ep)
        return uses_session

    def _apply_one_param(self, raw: str, ep: Endpoint) -> bool:
        tokens = raw.strip()
        if not tokens:
            return False
        # Session / auth types with no annotation.
        if re.search(r"\bHttpSession\b", tokens):
            return True
        if re.search(r"\b(Principal|Authentication)\b", tokens) and "@" not in tokens:
            if "bearerAuth" not in ep.security:
                ep.security.append("bearerAuth")
            return False

        anno = re.search(
            r"@(PathVariable|RequestParam|RequestHeader|RequestBody|CookieValue)"
            r"(?:\s*\(([^)]*)\))?",
            tokens,
        )
        # Variable name = last identifier in the fragment.
        idents = re.findall(r"[A-Za-z_]\w*", re.sub(r"@\w+(\([^)]*\))?", "", tokens))
        var_name = idents[-1] if idents else "param"
        type_token = idents[-2] if len(idents) >= 2 else "String"
        schema_type = _java_type_to_openapi(type_token)

        if not anno:
            return False
        kind = anno.group(1)
        anno_args = anno.group(2) or ""

        if kind == "RequestBody":
            if ep.method in ("post", "put", "patch"):
                ep.request_body = RequestBody(
                    description=f"Request body ({type_token}).",
                    schema_ref=type_token,
                )
            return False

        name_override = _extract_named(anno_args) or var_name
        required = True
        if kind == "RequestParam":
            if re.search(r"required\s*=\s*false", anno_args) or "defaultValue" in anno_args:
                required = False
            location = IN_QUERY
        elif kind == "RequestHeader":
            required = "required = false" not in anno_args.replace(" ", " ")
            location = IN_HEADER
        elif kind == "CookieValue":
            location = "cookie"
        else:  # PathVariable
            location = IN_PATH
            required = True

        ep.parameters.append(
            Parameter(
                name=name_override,
                location=location,
                required=required,
                schema_type=schema_type,
            )
        )
        return False


def _extract_named(anno_args: str) -> str:
    if not anno_args:
        return ""
    m = re.search(r"(?:value|name)\s*=\s*\"([^\"]*)\"", anno_args)
    if m:
        return m.group(1)
    m = re.search(r"^\s*\"([^\"]*)\"", anno_args)
    return m.group(1) if m else ""
