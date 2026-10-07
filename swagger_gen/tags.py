"""Display names for OpenAPI tags so Swagger UI groups by readable resources."""

from __future__ import annotations

import re

_SUFFIXES = (
    "RestController",
    "Controller",
    "Resource",
    "Endpoint",
    "Endpoints",
    "Handler",
    "ViewSet",
    "View",
)
_GENERIC_MODULE_TAGS = {
    "App",
    "Main",
    "Index",
    "Urls",
    "Application",
    "Api",
    "API",
}
# Path segments that are a version or gateway prefix, not a sub-project.
_SKIP_SEGMENTS = {
    "api",
    "apis",
    "rest",
    "v1",
    "v2",
    "v3",
    "v4",
    "internal",
    "public",
}


def pretty_tag(raw: str) -> str:
    """Turn analyzer tags (file names, class names) into a short group label."""
    text = (raw or "").strip()
    if not text:
        return "API"
    text = text.replace("\\", "/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    if "." in text and not text.startswith("."):
        text = text.rsplit(".", 1)[0]
    # Strip Java/Python class suffixes only on a single CamelCase token.
    if "-" not in text and "_" not in text and " " not in text:
        for suffix in _SUFFIXES:
            if len(text) > len(suffix) and text.endswith(suffix):
                text = text[: -len(suffix)]
                break
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "API"
    return " ".join(part.capitalize() if part.islower() else part for part in text.split())


def subproject_name(path: str, tags: list[str] | None, project: str | None = None) -> str:
    """Resource group under a repo: a real tag, otherwise the first path segment."""
    proj = pretty_tag(project or "")
    for tag in tags or []:
        label = pretty_tag(tag)
        if not label or label in _GENERIC_MODULE_TAGS:
            continue
        if proj and label == proj:
            continue
        return label
    for raw in (path or "").strip("/").split("/"):
        if not raw or raw.startswith("{") or raw.startswith(":"):
            continue
        if raw.lower() in _SKIP_SEGMENTS:
            continue
        return pretty_tag(raw)
    return proj or "API"


def operation_tags(tags: list[str] | None, project: str | None = None) -> list[str]:
    """Unique pretty tags, preserving order. Always at least one group."""
    out: list[str] = []
    for tag in tags or []:
        label = pretty_tag(tag)
        if label not in out:
            out.append(label)
    if project:
        proj = pretty_tag(project)
        if not out or (len(out) == 1 and out[0] in _GENERIC_MODULE_TAGS):
            return [proj]
    return out or ["API"]
