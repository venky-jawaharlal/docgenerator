"""Command-line entry point.

Usage:
    python -m swagger_gen --config config.yaml
    python -m swagger_gen --repo /path/to/local --repo https://github.com/org/svc.git \\
        --server https://svc.example.com
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

from .config import Config, ConfigError, OutputConfig, RepoConfig, load_config
from .git_source import looks_like_git_url, repo_name_from_url
from .openapi_builder import build_openapi
from .output import write_index, write_spec
from .scanner import cache_root, scan_all
from .servers import merge_servers, parse_server_entries, redact_url, servers_from_host
from .validate import ping_url, validate_openapi_doc


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="swagger_gen",
        description=(
            "Generate OpenAPI/Swagger docs from local repositories and/or git URLs. "
            "Set a deployment host so Swagger UI Try-it-out targets the live service."
        ),
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
        "--repo", action="append", dest="repos", metavar="PATH_OR_URL",
        help=(
            "Analyze a local directory or a git URL (repeatable). "
            "Skips the config file's repo list."
        ),
    )
    p.add_argument(
        "--ref",
        help="Git branch, tag, or commit to check out when --repo is a git URL.",
    )
    p.add_argument(
        "--server", action="append", dest="servers", metavar="URL",
        help=(
            "Deployment host for Try-it-out requests (repeatable). "
            "Applied to every source when using --repo; with --config, "
            "prepended so it becomes the default host."
        ),
    )
    p.add_argument(
        "--host",
        help="Shorthand for a single --server URL.",
    )
    p.add_argument(
        "--validate", action="store_true",
        help="Check each generated document (structure + deployed server present).",
    )
    p.add_argument(
        "--ping-servers", action="store_true",
        help=(
            "HTTP-ping each operator-configured deployment host (not URLs "
            "imported from source specs). Blocks redirects and metadata IPs."
        ),
    )
    p.add_argument(
        "--clean-cache", action="store_true",
        help="Delete the git clone cache after generation (recommended in CI).",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose per-endpoint logging.",
    )
    return p


def _cli_servers(args):
    return merge_servers(
        servers_from_host(getattr(args, "host", None)),
        parse_server_entries(getattr(args, "servers", None)),
    )


def _config_from_args(args) -> Config:
    cli_servers = _cli_servers(args)
    if args.repos:
        repos: list[RepoConfig] = []
        for raw in args.repos:
            if looks_like_git_url(raw):
                repo = RepoConfig(
                    name=repo_name_from_url(raw),
                    url=raw.strip(),
                    ref=args.ref,
                    servers=list(cli_servers),
                )
            else:
                path = str(Path(raw).expanduser().resolve())
                repo = RepoConfig(
                    name=Path(path).name or "repo",
                    path=path,
                    servers=list(cli_servers),
                )
            repos.append(repo)
        config = Config(output=OutputConfig(), repos=repos)
    else:
        config = load_config(args.config)
        if cli_servers:
            for repo in config.repos:
                repo.servers = merge_servers(cli_servers, repo.servers)
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
    sys.stdout.flush()
    results = scan_all(config)

    index_entries = []
    total_endpoints = 0
    had_error = False
    validation_failed = False

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
        origin = redact_url(repo.url) if repo.url else (repo.resolved_path or repo.path)
        extra = f"  source={origin}" if origin else ""
        print(f"  [ok]   {repo.name}: {endpoint_count} endpoints ({fw}){extra}")
        sys.stdout.flush()
        if spec.servers:
            hosts = ", ".join(redact_url(s.url) for s in spec.servers)
            print(f"         host: {hosts}")
        else:
            print(
                "         warning: no deployment host configured; "
                "Swagger UI Try-it-out will target this page's origin (often localhost). "
                "Set `host`/`servers` in config or pass --server.",
                file=sys.stderr,
            )
        if args.verbose:
            for ep in sorted(spec.endpoints, key=lambda e: (e.path, e.method)):
                sec = f"  auth={','.join(ep.security)}" if ep.security else ""
                print(f"         {ep.method.upper():6} {ep.path}{sec}")
            for note in spec.notes:
                print(f"         note: {note[:500]}")

        if args.validate:
            issues = validate_openapi_doc(doc)
            if issues:
                validation_failed = True
                for issue in issues:
                    print(f"         validate: {issue}", file=sys.stderr)
            else:
                print("         validate: ok")

        if args.ping_servers:
            # Only operator-configured hosts — never URLs imported from a spec.
            ping_targets = list(repo.servers)
            if not ping_targets:
                print("         ping: skipped (no host configured)")
            for server in ping_targets:
                ok, detail = ping_url(server.url)
                flag = "ok" if ok else "fail"
                print(f"         ping [{flag}]: {detail}")
                if not ok:
                    validation_failed = True

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
                "servers": [s.url for s in spec.servers],
            }
        )

    if config.output.combined and index_entries:
        combined = _combine(results)
        if args.validate:
            issues = validate_openapi_doc(combined)
            for issue in issues:
                print(f"  [combined] validate: {issue}", file=sys.stderr)
            if issues:
                validation_failed = True
        write_spec(combined, out_dir, "combined", config.output.formats)

    if "html" in config.output.formats and index_entries:
        index_path = write_index(out_dir, index_entries)
        print(f"\nIndex: {index_path}")

    print(f"Done. {total_endpoints} endpoints across {len(index_entries)} service(s).")
    print(f"Output written to: {out_dir}")

    if args.clean_cache:
        import shutil

        cache = cache_root(config)
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
            print(f"Clone cache removed: {cache}")

    if had_error and not index_entries:
        return 1
    if validation_failed:
        return 1
    return 0


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
            # Path-level servers so Try-it-out hits this service's deployed host.
            if spec.servers and not clone.servers:
                clone.servers = list(spec.servers)
            merged.add_endpoint(clone)
        for name, scheme in spec.security_schemes.items():
            merged.add_security_scheme(scheme)
        for name, schema in spec.schemas.items():
            merged.schemas.setdefault(name, schema)
        for note in spec.notes:
            merged.add_note(f"[{spec.name}] {note}")
    return build_openapi(merged)


if __name__ == "__main__":
    raise SystemExit(main())
