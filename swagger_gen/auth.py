"""Deployment-instance authentication for generated docs and live checks.

Secrets are resolved from the environment (preferred) or inline config values.
They are never written into OpenAPI YAML/JSON. HTML may optionally pre-authorize
Swagger UI when ``embed`` is enabled.

When ``token_url`` is set, a live access token is fetched (OAuth2 client
credentials or password grant) so ``--check-api`` / ``--ping-servers`` can
authenticate against the real instance.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Optional

from .models import ApiSpec, SecurityScheme
from .servers import is_blocked_ping_host, is_localhost_url, normalize_server_url, redact_url

APPLY_TO = ("all", "detected", "none")
AUTH_KINDS = ("bearer", "basic", "apikey", "header", "oauth2")
OAUTH_GRANTS = ("client_credentials", "password")

# Only ${VAR} is always expanded. Bare $VAR expands only when VAR is set, so a
# literal password like p$ssword is not truncated to "p".
_BRACED_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BARE_ENV = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

_TOKEN_TIMEOUT_SEC = 12.0


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass
class AuthConfig:
    type: str = ""
    scheme: str = ""
    token: str = ""
    token_env: str = ""
    username: str = ""
    username_env: str = ""
    password: str = ""
    password_env: str = ""
    api_key: str = ""
    api_key_env: str = ""
    header: str = ""
    apply_to: str = "all"
    probe_path: str = ""
    embed: bool = False
    token_url: str = ""
    grant: str = ""
    client_id: str = ""
    client_id_env: str = ""
    client_secret: str = ""
    client_secret_env: str = ""
    scope: str = ""
    audience: str = ""
    _cached_token: str = field(default="", repr=False, compare=False)
    _token_error: str = field(default="", repr=False, compare=False)

    def normalized_type(self) -> str:
        raw = (self.type or "").strip().lower().replace("-", "_")
        if raw in ("jwt",):
            return "bearer"
        if raw in ("api_key",):
            return "apikey"
        if raw in ("oauth", "oauth2", "client_credentials"):
            return "oauth2"
        return raw

    def oauth_grant(self) -> str:
        grant = (self.grant or "").strip().lower().replace("-", "_")
        if grant in OAUTH_GRANTS:
            return grant
        if self.normalized_type() == "oauth2" or self.token_url:
            return "password" if (self.username_env or self.username) else "client_credentials"
        return ""

    def scheme_name(self, spec: ApiSpec | None = None) -> str:
        if self.scheme.strip():
            return self.scheme.strip()
        kind = self.normalized_type()
        if spec:
            if kind == "oauth2":
                for name, scheme in spec.security_schemes.items():
                    if scheme.type == "oauth2":
                        return name
                return "oauth2"
            if kind == "bearer":
                for name, scheme in spec.security_schemes.items():
                    if scheme.type == "http" and (scheme.scheme or "bearer") == "bearer":
                        return name
            if kind == "basic":
                for name, scheme in spec.security_schemes.items():
                    if scheme.type == "http" and scheme.scheme == "basic":
                        return name
            if kind in ("apikey", "header"):
                want = (
                    self.header
                    or ("X-API-Key" if kind == "apikey" else "Authorization")
                ).lower()
                for name, scheme in spec.security_schemes.items():
                    if scheme.type != "apiKey":
                        continue
                    loc = (scheme.location or "header").lower()
                    if loc != "header":
                        continue
                    if (scheme.param_name or "").lower() == want:
                        return name
        return {
            "bearer": "bearerAuth",
            "basic": "basicAuth",
            "apikey": "apiKey",
            "header": "customHeader",
            "oauth2": "oauth2",
        }.get(kind, "bearerAuth")


def expand_env(value: str) -> str:
    """Replace ``${VAR}`` always; replace ``$VAR`` only when VAR is set."""

    def _braced(match: re.Match) -> str:
        return os.environ.get(match.group(1), "")

    def _bare(match: re.Match) -> str:
        name = match.group(1)
        if name in os.environ:
            return os.environ[name]
        return match.group(0)

    text = _BRACED_ENV.sub(_braced, value or "")
    return _BARE_ENV.sub(_bare, text)


def _from_env(direct: str, env_name: str) -> str:
    if env_name:
        got = os.environ.get(env_name, "")
        if got:
            return got
    return expand_env(direct)


def resolved_secrets(auth: AuthConfig) -> dict[str, str]:
    """Return resolved credential fields (may be empty). Never log these."""
    return {
        "token": _from_env(auth.token, auth.token_env),
        "username": _from_env(auth.username, auth.username_env),
        "password": _from_env(auth.password, auth.password_env),
        "api_key": _from_env(auth.api_key, auth.api_key_env),
        "client_id": _from_env(auth.client_id, auth.client_id_env),
        "client_secret": _from_env(auth.client_secret, auth.client_secret_env),
    }


def _can_fetch_token(auth: AuthConfig) -> bool:
    if not (auth.token_url or auth.normalized_type() == "oauth2"):
        return False
    secrets = resolved_secrets(auth)
    if secrets["token"]:
        return True
    grant = auth.oauth_grant()
    if grant == "password":
        return bool(secrets["username"] and secrets["password"])
    return bool(secrets["client_id"] and secrets["client_secret"])


def has_credentials(auth: AuthConfig | None) -> bool:
    if not auth or not auth.normalized_type():
        return False
    secrets = resolved_secrets(auth)
    kind = auth.normalized_type()
    if kind == "basic":
        return bool(secrets["username"] and secrets["password"])
    if kind == "apikey":
        return bool(secrets["api_key"] or secrets["token"])
    if kind == "oauth2" or auth.token_url:
        return _can_fetch_token(auth)
    return bool(secrets["token"] or secrets["api_key"])


def missing_credential_hint(auth: AuthConfig) -> str:
    kind = auth.normalized_type()
    if kind == "basic":
        names = [auth.username_env or "username", auth.password_env or "password"]
        return "set " + " and ".join(names)
    if kind == "oauth2" or auth.token_url:
        grant = auth.oauth_grant()
        if grant == "password":
            return "set username_env and password_env (and token_url) for the password grant"
        return "set client_id_env and client_secret_env (and token_url)"
    env = auth.token_env or auth.api_key_env
    if env:
        return f"export {env}=…"
    return "set token / token_env (or api_key_env)"


def parse_auth_entry(raw, default: AuthConfig | None = None) -> Optional[AuthConfig]:
    if raw is None:
        if not default:
            return None
        return replace(default, _cached_token="", _token_error="")
    if isinstance(raw, str):
        raw = {"type": raw}
    if not isinstance(raw, dict):
        return default
    base = (
        replace(default, _cached_token="", _token_error="") if default else AuthConfig()
    )
    type_val = str(raw.get("type") or base.type or "").strip()
    apply_to = str(raw.get("apply_to") or base.apply_to or "all").strip().lower()
    if apply_to not in APPLY_TO:
        apply_to = "all"
    embed = raw.get("embed")
    embed_flag = base.embed if embed is None else bool(embed)
    grant = str(raw.get("grant") or base.grant or "").strip()
    return AuthConfig(
        type=type_val,
        scheme=str(raw.get("scheme") or base.scheme or ""),
        token=str(raw.get("token") or base.token or ""),
        token_env=str(raw.get("token_env") or base.token_env or ""),
        username=str(raw.get("username") or base.username or ""),
        username_env=str(raw.get("username_env") or base.username_env or ""),
        password=str(raw.get("password") or base.password or ""),
        password_env=str(raw.get("password_env") or base.password_env or ""),
        api_key=str(raw.get("api_key") or base.api_key or ""),
        api_key_env=str(raw.get("api_key_env") or base.api_key_env or ""),
        header=str(raw.get("header") or base.header or ""),
        apply_to=apply_to,
        probe_path=str(raw.get("probe_path") or base.probe_path or ""),
        embed=embed_flag,
        token_url=str(raw.get("token_url") or base.token_url or ""),
        grant=grant,
        client_id=str(raw.get("client_id") or base.client_id or ""),
        client_id_env=str(raw.get("client_id_env") or base.client_id_env or ""),
        client_secret=str(raw.get("client_secret") or base.client_secret or ""),
        client_secret_env=str(raw.get("client_secret_env") or base.client_secret_env or ""),
        scope=str(raw.get("scope") or base.scope or ""),
        audience=str(raw.get("audience") or base.audience or ""),
    )


def auth_from_cli(
    auth_type: str | None,
    token: str | None = None,
    token_env: str | None = None,
    username: str | None = None,
    username_env: str | None = None,
    password_env: str | None = None,
    api_key_env: str | None = None,
    header: str | None = None,
    apply_to: str | None = None,
    probe_path: str | None = None,
    embed: bool | None = None,
    token_url: str | None = None,
    grant: str | None = None,
    client_id_env: str | None = None,
    client_secret_env: str | None = None,
    scope: str | None = None,
) -> Optional[AuthConfig]:
    if not any(
        (
            auth_type,
            token,
            token_env,
            username_env,
            api_key_env,
            token_url,
            client_id_env,
        )
    ):
        return None
    kind = auth_type or ("oauth2" if token_url else "bearer")
    return AuthConfig(
        type=kind,
        token=token or "",
        token_env=token_env or "",
        username=username or "",
        username_env=username_env or "",
        password_env=password_env or "",
        api_key_env=api_key_env or "",
        header=header or "",
        apply_to=apply_to or "all",
        probe_path=probe_path or "",
        embed=bool(embed) if embed is not None else False,
        token_url=token_url or "",
        grant=grant or "",
        client_id_env=client_id_env or "",
        client_secret_env=client_secret_env or "",
        scope=scope or "",
    )


def to_security_scheme(auth: AuthConfig, spec: ApiSpec) -> SecurityScheme:
    kind = auth.normalized_type()
    name = auth.scheme_name(spec)
    if kind == "basic":
        return SecurityScheme(
            name=name,
            type="http",
            scheme="basic",
            description="HTTP Basic authentication (username and password).",
        )
    if kind == "apikey":
        return SecurityScheme(
            name=name,
            type="apiKey",
            location="header",
            param_name=auth.header or "X-API-Key",
            description="API key sent as a request header.",
        )
    if kind == "header":
        return SecurityScheme(
            name=name,
            type="apiKey",
            location="header",
            param_name=auth.header or "Authorization",
            description="Custom authentication header.",
        )
    if kind == "oauth2":
        grant = auth.oauth_grant() or "client_credentials"
        flow_key = "password" if grant == "password" else "clientCredentials"
        scopes = {}
        for item in (auth.scope or "").split():
            scopes[item] = item
        flow: dict = {"scopes": scopes}
        if auth.token_url:
            flow["tokenUrl"] = auth.token_url
        return SecurityScheme(
            name=name,
            type="oauth2",
            description="OAuth2 access token obtained from the configured token URL.",
            flows={flow_key: flow},
        )
    description = "JWT / bearer token (Authorization: Bearer <token>)."
    if auth.token_url:
        description += f" Live checks fetch a token from {redact_url(auth.token_url)}."
    return SecurityScheme(
        name=name,
        type="http",
        scheme="bearer",
        bearer_format="JWT",
        description=description,
    )


def apply_auth_to_spec(spec: ApiSpec, auth: AuthConfig | None) -> None:
    """Ensure the OpenAPI document declares the configured auth mechanism."""
    if not auth or not auth.normalized_type():
        return
    if auth.normalized_type() not in AUTH_KINDS:
        spec.add_note(
            f"Unknown auth type '{auth.type}' (ignored). "
            "Use bearer, basic, apikey, header, or oauth2."
        )
        return
    scheme = to_security_scheme(auth, spec)
    spec.add_security_scheme(scheme)
    # OAuth2 Try-it-out still sends a bearer access token; keep http bearer too
    # so Authorize + --embed-auth work after a token has been fetched.
    if auth.normalized_type() == "oauth2":
        bearer = SecurityScheme(
            name="bearerAuth",
            type="http",
            scheme="bearer",
            bearer_format="JWT",
            description="Access token from the OAuth2 token URL (Authorization: Bearer).",
        )
        spec.add_security_scheme(bearer)

    if auth.apply_to == "none":
        spec.add_note(
            f"Auth scheme {scheme.name} ({auth.normalized_type()}) documented; "
            "not attached to operations (apply_to: none)."
        )
        return
    if auth.apply_to == "all":
        names = [scheme.name]
        if auth.normalized_type() == "oauth2" and "bearerAuth" not in names:
            names.append("bearerAuth")
        spec.default_security = names[:1]
        for ep in spec.endpoints:
            for name in names:
                if name not in ep.security:
                    ep.security.append(name)
        spec.add_note(
            f"Auth {scheme.name} ({auth.normalized_type()}) attached to all "
            "operations so Try-it-out sends credentials to the deployed host."
        )
        return

    # apply_to: detected — attach the live scheme to operations already marked secured.
    attached = 0
    for ep in spec.endpoints:
        if not ep.security:
            continue
        if scheme.name not in ep.security:
            ep.security.append(scheme.name)
        attached += 1
    if attached:
        spec.add_note(
            f"Auth {scheme.name} ({auth.normalized_type()}) attached to "
            f"{attached} operation(s) already marked as secured."
        )
    else:
        spec.add_note(
            f"Auth scheme {scheme.name} documented; no secured operations were "
            "detected so nothing was attached. Use apply_to: all to send auth "
            "on every Try-it-out call."
        )


def _fetch_oauth_token(auth: AuthConfig) -> str:
    """POST to token_url. Returns an access_token or sets auth._token_error."""
    if auth._cached_token:
        return auth._cached_token
    url = normalize_server_url(auth.token_url)
    if not url:
        auth._token_error = "token_url is missing or not http(s)"
        return ""
    if is_localhost_url(url) or is_blocked_ping_host(url):
        auth._token_error = f"token_url blocked (loopback/metadata): {redact_url(url)}"
        return ""
    secrets = resolved_secrets(auth)
    grant = auth.oauth_grant() or "client_credentials"
    form: dict[str, str] = {"grant_type": grant}
    if auth.scope:
        form["scope"] = auth.scope
    if auth.audience:
        form["audience"] = auth.audience
    headers = {
        "User-Agent": "swagger_gen-host-check",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    if grant == "password":
        form["username"] = secrets["username"]
        form["password"] = secrets["password"]
        if secrets["client_id"]:
            form["client_id"] = secrets["client_id"]
        if secrets["client_secret"]:
            form["client_secret"] = secrets["client_secret"]
    else:
        form["client_id"] = secrets["client_id"]
        form["client_secret"] = secrets["client_secret"]

    body = urllib.parse.urlencode(form).encode("utf-8")
    opener = urllib.request.build_opener(_NoRedirectHandler)
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with opener.open(req, timeout=_TOKEN_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        auth._token_error = f"token endpoint returned {exc.code} for {redact_url(url)}"
        return ""
    except Exception as exc:
        auth._token_error = f"token fetch failed: {type(exc).__name__}"
        return ""
    token = ""
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            token = str(payload.get("access_token") or payload.get("id_token") or "")
    except json.JSONDecodeError:
        parsed = urllib.parse.parse_qs(raw)
        token = (parsed.get("access_token") or [""])[0]
    if not token:
        auth._token_error = f"token endpoint at {redact_url(url)} did not return access_token"
        return ""
    if status and int(status) >= 400:
        auth._token_error = f"token endpoint returned {status} for {redact_url(url)}"
        return ""
    auth._cached_token = token
    auth._token_error = ""
    return token


def access_token(auth: AuthConfig | None) -> str:
    """Static token, API key, or a freshly fetched OAuth2 access token."""
    if not auth:
        return ""
    secrets = resolved_secrets(auth)
    static = secrets["token"] or secrets["api_key"]
    if static:
        return static
    if auth.token_url or auth.normalized_type() == "oauth2":
        if not auth.token_url:
            auth._token_error = "oauth2 auth requires token_url"
            return ""
        return _fetch_oauth_token(auth)
    return ""


def request_headers(auth: AuthConfig | None) -> dict[str, str]:
    """HTTP headers for live host checks. Empty when credentials are missing."""
    if not auth or not has_credentials(auth):
        return {}
    secrets = resolved_secrets(auth)
    kind = auth.normalized_type()
    if kind == "basic":
        blob = base64.b64encode(
            f"{secrets['username']}:{secrets['password']}".encode("utf-8")
        ).decode("ascii")
        return {"Authorization": f"Basic {blob}"}
    if kind == "apikey":
        value = secrets["api_key"] or secrets["token"]
        if not value:
            return {}
        return {auth.header or "X-API-Key": value}
    if kind == "header":
        value = secrets["token"] or secrets["api_key"]
        if not value:
            return {}
        return {auth.header or "Authorization": value}
    token = access_token(auth)
    if not token:
        return {}
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    return {"Authorization": f"Bearer {token}"}


def interceptor_header(auth: AuthConfig | None) -> dict[str, str] | None:
    headers = request_headers(auth)
    if not headers:
        return None
    name, value = next(iter(headers.items()))
    return {"name": name, "value": value}


def swagger_prefill(auth: AuthConfig | None, spec: ApiSpec | None = None) -> dict:
    """Payload for Swagger UI preauthorize*. Empty unless embed + credentials."""
    if not auth or not auth.embed or not has_credentials(auth):
        return {}
    secrets = resolved_secrets(auth)
    name = auth.scheme_name(spec)
    kind = auth.normalized_type()
    schemes: dict = {}
    if kind == "basic":
        schemes[name] = {
            "type": "basic",
            "username": secrets["username"],
            "password": secrets["password"],
        }
    else:
        value = access_token(auth)
        if kind == "bearer" and value.lower().startswith("bearer "):
            value = value[7:].strip()
        if value:
            # Swagger UI preauthorizeApiKey works for http bearer and apiKey.
            schemes[name if kind != "oauth2" else "bearerAuth"] = {
                "type": "apiKey",
                "value": value,
            }
    header = interceptor_header(auth)
    out: dict = {"schemes": schemes}
    if header:
        out["header"] = header
    return out


def describe_auth(auth: AuthConfig | None) -> str:
    if not auth or not auth.normalized_type():
        return ""
    kind = auth.normalized_type()
    if auth.token_url and kind != "oauth2":
        kind = f"{kind}+token_url"
    ready = "ready" if has_credentials(auth) else "scheme only"
    return f"{kind} ({ready})"
