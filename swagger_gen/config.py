"""Configuration loading.

The tool is driven by a single YAML config file. Repositories can be listed
inline under ``repos:`` and/or pulled from a plain-text file (one path or git
URL per line) referenced by ``repos_file:``. Anything not specified falls back
to a sensible default so a minimal config is just a list of sources.

Each repo may be a local directory (``path``) and/or a remote git URL
(``url``). Deployment hosts go on ``host`` / ``servers`` so Swagger UI
Try-it-out targets the real service instead of localhost.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .auth import AuthConfig, parse_auth_entry
from .git_source import looks_like_git_url, repo_name_from_url
from .models import Server
from .servers import merge_servers, parse_server_entries, servers_from_host

DEFAULT_FORMATS = ["yaml", "json", "html"]
DEFAULT_CLONE_CACHE = "./.swagger-gen-cache"


@dataclass
class OutputConfig:
    directory: str = "./swagger-output"
    formats: list[str] = field(default_factory=lambda: list(DEFAULT_FORMATS))
    per_repo: bool = True
    combined: bool = True  # also emit a merged spec across all repos


@dataclass
class RepoConfig:
    name: str
    path: str = ""
    url: Optional[str] = None
    ref: Optional[str] = None  # branch, tag, or commit for git sources
    title: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    base_path: Optional[str] = None
    host: Optional[str] = None
    servers: list[Server] = field(default_factory=list)
    auth: Optional[AuthConfig] = None
    # Force a set of frameworks instead of auto-detecting.
    frameworks: Optional[list[str]] = None
    # Extra directories (relative to repo) to skip during scanning.
    exclude: list[str] = field(default_factory=list)
    # Filled in after a git clone / local path resolve.
    resolved_path: str = ""


@dataclass
class Config:
    output: OutputConfig = field(default_factory=OutputConfig)
    repos: list[RepoConfig] = field(default_factory=list)
    # Global excludes applied to every repo in addition to built-in defaults.
    exclude: list[str] = field(default_factory=list)
    config_dir: str = "."
    clone_cache: str = DEFAULT_CLONE_CACHE
    default_servers: list[Server] = field(default_factory=list)
    default_auth: Optional[AuthConfig] = None


class ConfigError(Exception):
    pass


def _derive_name(path_or_url: str) -> str:
    if looks_like_git_url(path_or_url):
        return repo_name_from_url(path_or_url)
    name = os.path.basename(os.path.normpath(path_or_url.rstrip("/")))
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repo"


def _parse_source_line(line: str) -> tuple[Optional[str], str]:
    """Return (optional_name, path_or_url) from a repos_file line."""
    name = None
    source = line
    for sep in ("=", "\t"):
        if sep in line:
            name, source = (p.strip() for p in line.split(sep, 1))
            break
    return name, source


def _load_repos_file(repos_file: Path) -> list[RepoConfig]:
    if not repos_file.exists():
        raise ConfigError(f"repos_file not found: {repos_file}")
    repos: list[RepoConfig] = []
    for raw in repos_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, source = _parse_source_line(line)
        source = os.path.expanduser(source)
        repos.append(_repo_from_source(source, name=name))
    return repos


def _repo_from_source(source: str, name: Optional[str] = None) -> RepoConfig:
    if looks_like_git_url(source):
        return RepoConfig(
            name=name or _derive_name(source),
            url=source,
        )
    return RepoConfig(
        name=name or _derive_name(source),
        path=source,
    )


def _apply_servers(repo: RepoConfig, extra: list[Server] | None = None) -> None:
    declared = merge_servers(
        servers_from_host(repo.host),
        list(repo.servers),
        extra or [],
    )
    repo.servers = declared


def _parse_repo_entry(
    entry, default_servers: list[Server], default_auth: AuthConfig | None = None
) -> RepoConfig:
    if isinstance(entry, str):
        repo = _repo_from_source(os.path.expanduser(entry))
        _apply_servers(repo, default_servers)
        repo.auth = default_auth
        return repo
    if not isinstance(entry, dict):
        raise ConfigError(f"Invalid repo entry: {entry!r}")

    path = os.path.expanduser(str(entry["path"])) if entry.get("path") else ""
    url = str(entry["url"]).strip() if entry.get("url") else None
    if path and looks_like_git_url(path) and not url:
        url = path
        path = ""
    if not path and not url:
        raise ConfigError(
            f"Repo entry needs a local 'path' or a git 'url': {entry!r}"
        )

    source_for_name = path or url or "repo"
    repo = RepoConfig(
        name=entry.get("name") or _derive_name(source_for_name),
        path=path,
        url=url,
        ref=str(entry["ref"]) if entry.get("ref") else None,
        title=entry.get("title"),
        version=entry.get("version"),
        description=entry.get("description"),
        base_path=entry.get("base_path"),
        host=str(entry["host"]) if entry.get("host") else None,
        servers=parse_server_entries(entry.get("servers")),
        auth=parse_auth_entry(entry.get("auth"), default_auth),
        frameworks=entry.get("frameworks"),
        exclude=list(entry.get("exclude", []) or []),
    )
    _apply_servers(repo, default_servers)
    return repo


def load_config(config_path: str) -> Config:
    path = Path(config_path).expanduser()
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - passthrough
        raise ConfigError(f"Failed to parse YAML config: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Top-level config must be a mapping.")

    config_dir = path.parent

    out_raw = data.get("output", {}) or {}
    output = OutputConfig(
        directory=out_raw.get("directory", OutputConfig.directory),
        formats=[f.lower() for f in out_raw.get("formats", DEFAULT_FORMATS)],
        per_repo=bool(out_raw.get("per_repo", True)),
        combined=bool(out_raw.get("combined", True)),
    )
    for fmt in output.formats:
        if fmt not in ("yaml", "yml", "json", "html"):
            raise ConfigError(f"Unsupported output format: {fmt}")

    default_servers = merge_servers(
        servers_from_host(data.get("default_host"), description="Default deployed host"),
        parse_server_entries(data.get("default_servers") or data.get("servers")),
    )
    default_auth = parse_auth_entry(data.get("default_auth") or data.get("auth"))

    repos: list[RepoConfig] = []
    for entry in data.get("repos", []) or []:
        repos.append(_parse_repo_entry(entry, default_servers, default_auth))

    repos_file = data.get("repos_file")
    if repos_file:
        rf = Path(os.path.expanduser(str(repos_file)))
        if not rf.is_absolute():
            rf = config_dir / rf
        for repo in _load_repos_file(rf):
            _apply_servers(repo, default_servers)
            if default_auth and not repo.auth:
                repo.auth = default_auth
            repos.append(repo)

    if not repos:
        raise ConfigError(
            "No repositories configured. Add a 'repos:' list or 'repos_file:'."
        )

    # Resolve relative *local* paths against the config file's directory.
    # Git URLs are left as-is and cloned later by the scanner.
    for repo in repos:
        if repo.path and not looks_like_git_url(repo.path):
            p = Path(repo.path)
            if not p.is_absolute():
                repo.path = str((config_dir / p).resolve())

    clone_cache = str(data.get("clone_cache") or DEFAULT_CLONE_CACHE)

    return Config(
        output=output,
        repos=repos,
        exclude=list(data.get("exclude", []) or []),
        config_dir=str(config_dir.resolve()),
        clone_cache=clone_cache,
        default_servers=default_servers,
        default_auth=default_auth,
    )
