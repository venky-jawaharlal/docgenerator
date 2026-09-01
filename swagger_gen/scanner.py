"""Repository scanning orchestration.

For each configured repo: resolve a local path (cloning a git URL if needed),
build a cached scanning context, run every analyzer that detects its
framework, and fold the results into a single ``ApiSpec``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .analyzers import ALL_ANALYZERS, ANALYZERS_BY_NAME, RepoContext
from .config import Config, RepoConfig
from .git_source import SourceError, clone_or_update, looks_like_git_url
from .models import ApiSpec
from .servers import drop_localhost, merge_servers


@dataclass
class ScanResult:
    repo: RepoConfig
    spec: ApiSpec | None
    error: str | None = None


def resolve_repo(repo: RepoConfig, cache_root: Path) -> str:
    """Return a local directory for ``repo``, cloning from git when necessary."""
    local = repo.path
    if local and os.path.isdir(local):
        repo.resolved_path = local
        return local

    url = repo.url
    if not url and local and looks_like_git_url(local):
        url = local

    if url:
        cloned = clone_or_update(url, repo.ref, cache_root)
        repo.resolved_path = str(cloned)
        repo.url = url
        return str(cloned)

    missing = local or "(no path or url)"
    raise SourceError(f"Path not found: {missing}")


def scan_repo(repo: RepoConfig, global_exclude: list[str], cache_root: Path) -> ScanResult:
    try:
        root = resolve_repo(repo, cache_root)
    except SourceError as exc:
        return ScanResult(repo=repo, spec=None, error=str(exc))

    ctx = RepoContext(root, excludes=list(global_exclude) + list(repo.exclude))
    spec = ApiSpec(
        name=repo.name,
        title=repo.title or repo.name,
        version=repo.version or "1.0.0",
        description=repo.description or "",
        base_path=repo.base_path or "",
        servers=list(repo.servers),
    )

    # Choose analyzers: explicit override, else auto-detect.
    if repo.frameworks:
        selected = []
        for fw in repo.frameworks:
            analyzer = ANALYZERS_BY_NAME.get(fw)
            if analyzer:
                selected.append(analyzer)
            else:
                spec.add_note(f"Unknown framework in config: '{fw}' (ignored).")
    else:
        selected = [a for a in ALL_ANALYZERS if _safe_detect(a, ctx)]

    for analyzer in selected:
        try:
            analyzer.analyze(ctx, spec)
            spec.detected_frameworks.append(analyzer.display_name)
        except Exception as exc:  # keep going; one bad file shouldn't abort a repo
            spec.add_note(f"{analyzer.display_name} analysis error: {exc}")

    # Configured deployment hosts always win. Localhost entries imported from
    # an existing spec are dropped so Try-it-out cannot silently target loopback.
    # Non-local URLs are listed first so the deployed host is the Swagger UI default.
    if repo.servers:
        keep_cfg, local_cfg = drop_localhost(list(repo.servers))
        spec.servers = merge_servers(keep_cfg, local_cfg)
    else:
        keep, dropped = drop_localhost(spec.servers)
        spec.servers = keep
        if dropped:
            spec.add_note(
                "Dropped localhost/loopback server URL(s) imported from the "
                "source spec ("
                + ", ".join(s.url for s in dropped)
                + "). Set `host` or `servers` in config to the deployed service."
            )

    if not spec.endpoints and not spec.detected_frameworks:
        spec.add_note(
            "No supported web framework detected and no endpoints found. "
            "Supported: Spring, JAX-RS, FastAPI, Flask, Django, existing OpenAPI."
        )
    return ScanResult(repo=repo, spec=spec)


def _safe_detect(analyzer, ctx: RepoContext) -> bool:
    try:
        return analyzer.detect(ctx)
    except Exception:
        return False


def cache_root(config: Config) -> Path:
    cache = Path(config.clone_cache).expanduser()
    if not cache.is_absolute():
        cache = Path(config.config_dir) / cache
    return cache


def scan_all(config: Config) -> list[ScanResult]:
    root = cache_root(config)
    results: list[ScanResult] = []
    for repo in config.repos:
        results.append(scan_repo(repo, config.exclude, root))
    return results
