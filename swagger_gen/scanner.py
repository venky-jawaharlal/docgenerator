"""Repository scanning orchestration.

For each configured repo: build a cached scanning context, run every analyzer
that detects its framework, and fold the results into a single ``ApiSpec``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .analyzers import ALL_ANALYZERS, ANALYZERS_BY_NAME, RepoContext
from .config import Config, RepoConfig
from .models import ApiSpec


@dataclass
class ScanResult:
    repo: RepoConfig
    spec: ApiSpec | None
    error: str | None = None


def scan_repo(repo: RepoConfig, global_exclude: list[str]) -> ScanResult:
    if not os.path.isdir(repo.path):
        return ScanResult(repo=repo, spec=None, error=f"Path not found: {repo.path}")

    ctx = RepoContext(repo.path, excludes=list(global_exclude) + list(repo.exclude))
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


def scan_all(config: Config) -> list[ScanResult]:
    results: list[ScanResult] = []
    for repo in config.repos:
        results.append(scan_repo(repo, config.exclude))
    return results
