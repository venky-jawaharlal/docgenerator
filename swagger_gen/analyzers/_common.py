"""Small helpers shared across analyzers."""

from __future__ import annotations

import re


def join_paths(*parts: str) -> str:
    """Join URL path segments with exactly one slash and a leading slash."""
    cleaned = [p.strip() for p in parts if p and p.strip() not in ("", "/")]
    joined = "/" + "/".join(seg.strip("/") for seg in cleaned)
    joined = re.sub(r"/{2,}", "/", joined)
    if len(joined) > 1 and joined.endswith("/"):
        joined = joined.rstrip("/")
    return joined or "/"


def to_openapi_path(path: str) -> str:
    """Normalize framework-specific path params to OpenAPI ``{name}`` form."""
    # Flask/Werkzeug: <int:id>, <uuid:token>, <name>  ->  {id}, {token}, {name}
    path = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", path)
    # Spring regex constraints: {id:\\d+}  ->  {id}
    path = re.sub(r"\{([A-Za-z_][\w]*)\s*:[^}]+\}", r"{\1}", path)
    # Django/DRF: (?P<pk>[^/]+)  ->  {pk}
    path = re.sub(r"\(\?P<([^>]+)>[^)]*\)", r"{\1}", path)
    # Legacy Django: :id  ->  {id}
    path = re.sub(r":([A-Za-z_][\w]*)", r"{\1}", path)
    return path


def path_param_names(path: str) -> list[str]:
    """Return the ``{name}`` params present in an OpenAPI-style path."""
    return re.findall(r"\{([A-Za-z_][\w]*)\}", path)


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def humanize(identifier: str) -> str:
    """Turn ``getUserById`` / ``get_user_by_id`` into ``Get user by id``."""
    if not identifier:
        return ""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", identifier)
    spaced = spaced.replace("_", " ").replace("-", " ")
    spaced = re.sub(r"\s+", " ", spaced).strip().lower()
    return spaced[:1].upper() + spaced[1:] if spaced else ""
