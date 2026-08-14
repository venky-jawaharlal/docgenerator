"""Command-line entry point.

Usage:
    python -m swagger_gen --config config.yaml
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

from .config import Config, ConfigError, OutputConfig, RepoConfig, load_config
from .openapi_builder import build_openapi
from .output import write_index, write_spec
from .scanner import scan_all


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="swagger_gen",
        description="Generate OpenAPI/Swagger docs for multiple local repos.",
    )
    p.add_argument(
        "-c", "--config", default="config.yaml",
        help="Path to YAML config file (default: config.yaml).",
    )
    p.add_argument(
        "-o", "--output",
        help="Override output directory from the config file.",
    )
    p.add_argument(
        "-f", "--formats", nargs="+", choices=["yaml", "json", "html"],
        help="Override output formats from the config file.",
    )
    p.add_argument(
        "--repo", action="append", dest="repos", metavar="PATH",
        help="Analyze an ad-hoc repo path (repeatable). Skips the config repos.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose per-endpoint logging.",
    )
    return p


def _config_from_args(args) -> Config:
    if args.repos:
        repos = [RepoConfig(name=Path(p).name or "repo", path=str(Path(p).expanduser().resolve())) for p in args.repos]
        config = Config(output=OutputConfig(), repos=repos)
    else:
        config = load_config(args.config)
    if args.output:
        config.output.directory = args.output
    if args.formats:
        config.output.formats = args.formats
    return config


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        config = _config_from_args(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out_dir = Path(config.output.directory).expanduser()
    if not out_dir.is_absolute():
        out_dir = Path(config.config_dir) / out_dir

    print(f"Scanning {len(config.repos)} repositor(y/ies)...")
    results = scan_all(config)

    index_entries = []
    total_endpoints = 0
    had_error = False

    for result in results:
        repo = result.repo
        if result.error or result.spec is None:
            had_error = True
            print(f"  [skip] {repo.name}: {result.error}")
            continue

        spec = result.spec
        doc = build_openapi(spec)
        endpoint_count = len(spec.endpoints)
        total_endpoints += endpoint_count

        fw = ", ".join(spec.detected_frameworks) or "none detected"
        print(f"  [ok]   {repo.name}: {endpoint_count} endpoints ({fw})")
        if args.verbose:
            for ep in sorted(spec.endpoints, key=lambda e: (e.path, e.method)):
                sec = f"  auth={','.join(ep.security)}" if ep.security else ""
                print(f"         {ep.method.upper():6} {ep.path}{sec}")
            for note in spec.notes:
                print(f"         note: {note}")

        written = {}
        if config.output.per_repo:
            written = write_spec(doc, out_dir, spec.name, config.output.formats)

        index_entries.append(
            {
                "title": spec.title,
                "version": spec.version,
                "endpoint_count": endpoint_count,
                "frameworks": spec.detected_frameworks,
                "html": written["html"].name if "html" in written else None,
            }
        )

    if config.output.combined and index_entries:
        combined = _combine(results)
        write_spec(combined, out_dir, "combined", config.output.formats)

    if "html" in config.output.formats and index_entries:
        index_path = write_index(out_dir, index_entries)
        print(f"\nIndex: {index_path}")

    print(f"Done. {total_endpoints} endpoints across {len(index_entries)} service(s).")
    print(f"Output written to: {out_dir}")
    return 1 if had_error and not index_entries else 0


def _combine(results) -> dict:
    """Merge every repo's spec into one document (paths namespaced by repo)."""
    from .models import ApiSpec

    merged = ApiSpec(name="combined", title="Combined API Documentation")
    for result in results:
        spec = result.spec
        if spec is None:
            continue
        for ep in spec.endpoints:
            clone = copy.deepcopy(ep)
            if spec.name not in clone.tags:
                clone.tags = [spec.name] + clone.tags
            merged.add_endpoint(clone)
        for name, scheme in spec.security_schemes.items():
            merged.add_security_scheme(scheme)
        for name, schema in spec.schemas.items():
            merged.schemas.setdefault(name, schema)
    return build_openapi(merged)


if __name__ == "__main__":
    raise SystemExit(main())
