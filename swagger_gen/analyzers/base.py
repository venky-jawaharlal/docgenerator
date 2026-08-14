"""Analyzer base class and per-repo scanning context.

An analyzer is responsible for one framework/tech stack. It declares whether it
applies to a repo (``detect``) and, if so, populates the shared ``ApiSpec``
(``analyze``). Adding support for a new stack is just a new subclass registered
in ``analyzers/__init__.py``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator

from ..models import ApiSpec

# Directories that never contain useful source and only slow scanning down.
DEFAULT_EXCLUDES = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env", "__pycache__",
    "target", "build", "dist", "out", ".gradle", ".idea", ".mvn", "bin",
    "site-packages", ".tox", ".pytest_cache", ".mypy_cache", "vendor",
    "coverage", ".next", ".terraform",
}


class RepoContext:
    """Cached view over a single repository's files."""

    def __init__(self, root: str, excludes: Iterable[str] = ()):
        self.root = Path(root)
        self.excludes = set(DEFAULT_EXCLUDES) | {e.strip("/ ") for e in excludes}
        self._file_cache: dict[str, list[Path]] = {}
        self._text_cache: dict[Path, str] = {}

    def _walk(self) -> Iterator[Path]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            # Prune excluded directories in-place so os.walk skips them.
            dirnames[:] = [d for d in dirnames if d not in self.excludes]
            for name in filenames:
                yield Path(dirpath) / name

    def files_with_ext(self, *exts: str) -> list[Path]:
        key = "|".join(sorted(e.lower() for e in exts))
        if key not in self._file_cache:
            wanted = {e.lower() if e.startswith(".") else "." + e.lower() for e in exts}
            self._file_cache[key] = [
                p for p in self._walk() if p.suffix.lower() in wanted
            ]
        return self._file_cache[key]

    def files_named(self, *names: str) -> list[Path]:
        wanted = {n.lower() for n in names}
        return [p for p in self._walk() if p.name.lower() in wanted]

    def read(self, path: Path) -> str:
        if path not in self._text_cache:
            try:
                self._text_cache[path] = path.read_text(
                    encoding="utf-8", errors="ignore"
                )
            except OSError:
                self._text_cache[path] = ""
        return self._text_cache[path]

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    @lru_cache(maxsize=1)
    def has_dependency(self, *needles: str) -> bool:  # type: ignore[override]
        """True if any needle appears in common dependency manifests."""
        manifests = self.files_named(
            "pom.xml", "build.gradle", "build.gradle.kts", "requirements.txt",
            "pyproject.toml", "setup.py", "Pipfile", "go.mod", "package.json",
        )
        blob = "\n".join(self.read(m) for m in manifests).lower()
        return any(n.lower() in blob for n in needles)


class BaseAnalyzer:
    #: Stable identifier used in config ``frameworks:`` overrides and reports.
    name: str = "base"
    #: Human-friendly label shown in generated docs.
    display_name: str = "Base"

    def detect(self, ctx: RepoContext) -> bool:
        """Return True if this framework appears to be used in the repo."""
        raise NotImplementedError

    def analyze(self, ctx: RepoContext, spec: ApiSpec) -> None:
        """Populate ``spec`` with endpoints, schemes and notes."""
        raise NotImplementedError
