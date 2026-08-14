"""Analyzer registry.

Order matters: the existing-spec importer runs first so authoritative,
hand-written specs seed the IR before heuristic analyzers add anything they
find. To support a new tech stack, implement a ``BaseAnalyzer`` subclass and add
it to ``ALL_ANALYZERS``.
"""

from __future__ import annotations

from .base import BaseAnalyzer, RepoContext
from .django import DjangoAnalyzer
from .existing_spec import ExistingSpecAnalyzer
from .fastapi import FastAPIAnalyzer
from .flask import FlaskAnalyzer
from .jaxrs import JaxRsAnalyzer
from .spring import SpringAnalyzer

ALL_ANALYZERS: list[BaseAnalyzer] = [
    ExistingSpecAnalyzer(),
    SpringAnalyzer(),
    JaxRsAnalyzer(),
    FastAPIAnalyzer(),
    FlaskAnalyzer(),
    DjangoAnalyzer(),
]

ANALYZERS_BY_NAME = {a.name: a for a in ALL_ANALYZERS}

__all__ = [
    "BaseAnalyzer",
    "RepoContext",
    "ALL_ANALYZERS",
    "ANALYZERS_BY_NAME",
]
