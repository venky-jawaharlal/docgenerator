"""Configuration loading.

The tool is driven by a single YAML config file. Repositories can be listed
inline under ``repos:`` and/or pulled from a plain-text file (one path per
line) referenced by ``repos_file:``. Anything not specified falls back to a
sensible default so a minimal config is just a list of paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

DEFAULT_FORMATS = ["yaml", "json", "html"]


@dataclass
class OutputConfig:
    directory: str = "./swagger-output"
    formats: list[str] = field(default_factory=lambda: list(DEFAULT_FORMATS))
    per_repo: bool = True
    combined: bool = True  # also emit a merged spec across all repos


@dataclass
class RepoConfig:
    name: str
    path: str
    title: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    base_path: Optional[str] = None
    servers: list[str] = field(default_factory=list)
    # Force a set of frameworks instead of auto-detecting.
    frameworks: Optional[list[str]] = None
    # Extra directories (relative to repo) to skip during scanning.
    exclude: list[str] = field(default_factory=list)


@dataclass
class Config:
    output: OutputConfig = field(default_factory=OutputConfig)
    repos: list[RepoConfig] = field(default_factory=list)
    # Global excludes applied to every repo in addition to built-in defaults.
    exclude: list[str] = field(default_factory=list)
    config_dir: str = "."


class ConfigError(Exception):
    pass


def _derive_name(path: str) -> str:
    name = os.path.basename(os.path.normpath(path))
    return name or "repo"


def _load_repos_file(repos_file: Path) -> list[RepoConfig]:
    if not repos_file.exists():
        raise ConfigError(f"repos_file not found: {repos_file}")
    repos: list[RepoConfig] = []
    for raw in repos_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Optional "name = path" or "name: path" syntax; otherwise bare path.
        name = None
        path = line
        for sep in ("=", "\t"):
            if sep in line:
                name, path = (p.strip() for p in line.split(sep, 1))
                break
        path = os.path.expanduser(path)
        repos.append(RepoConfig(name=name or _derive_name(path), path=path))
    return repos


def _parse_repo_entry(entry, config_dir: Path) -> RepoConfig:
    if isinstance(entry, str):
        path = os.path.expanduser(entry)
        return RepoConfig(name=_derive_name(path), path=path)
    if not isinstance(entry, dict):
        raise ConfigError(f"Invalid repo entry: {entry!r}")
    path = entry.get("path")
    if not path:
        raise ConfigError(f"Repo entry missing 'path': {entry!r}")
    path = os.path.expanduser(str(path))
    return RepoConfig(
        name=entry.get("name") or _derive_name(path),
        path=path,
        title=entry.get("title"),
        version=entry.get("version"),
        description=entry.get("description"),
        base_path=entry.get("base_path"),
        servers=list(entry.get("servers", []) or []),
        frameworks=entry.get("frameworks"),
        exclude=list(entry.get("exclude", []) or []),
    )


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

    repos: list[RepoConfig] = []
    for entry in data.get("repos", []) or []:
        repos.append(_parse_repo_entry(entry, config_dir))

    repos_file = data.get("repos_file")
    if repos_file:
        rf = Path(os.path.expanduser(str(repos_file)))
        if not rf.is_absolute():
            rf = config_dir / rf
        repos.extend(_load_repos_file(rf))

    if not repos:
        raise ConfigError(
            "No repositories configured. Add a 'repos:' list or 'repos_file:'."
        )

    # Resolve relative repo paths against the config file's directory.
    for repo in repos:
        p = Path(repo.path)
        if not p.is_absolute():
            repo.path = str((config_dir / p).resolve())

    return Config(
        output=output,
        repos=repos,
        exclude=list(data.get("exclude", []) or []),
        config_dir=str(config_dir.resolve()),
    )
