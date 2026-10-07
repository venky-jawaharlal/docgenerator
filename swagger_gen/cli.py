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

from .auth import (
    auth_from_cli,
    describe_auth,
    has_credentials,
    missing_credential_hint,
    request_headers,
    swagger_prefill,
)
from .config import Config, ConfigError, OutputConfig, RepoConfig, load_config
from .git_source import looks_like_git_url, repo_name_from_url
from .openapi_builder import build_openapi
from .output import _slug as spec_slug, write_spec
from .portal import operations_for_spec, write_portal
from .tags import pretty_tag
from .scanner import cache_root, scan_all
from .servers import merge_servers, parse_server_entries, redact_url, servers_from_host
from .validate import join_host_path, ping_url, validate_openapi_doc


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
        "--git-token-env",
        help="Env var with an HTTPS git token (PAT) used to clone private --repo URLs.",
    )
    p.add_argument(
        "--git-username-env",
        help="Env var with the git HTTPS username (default for a token is x-access-token).",
    )
    p.add_argument(
        "--git-password-env",
        help="Env var with the git HTTPS password, when not using a token.",
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
        "--auth-type",
        choices=["bearer", "jwt", "basic", "apikey", "header", "oauth2"],
        help="Auth mechanism for the deployed host (applied to every repo).",
    )
    p.add_argument(
        "--token-env",
        help="Environment variable holding a bearer token or API key.",
    )
    p.add_argument(
        "--token",
        help="Bearer token or API key value (prefer --token-env so it stays out of the process list).",
    )
    p.add_argument(
        "--username-env",
        help="Environment variable holding the Basic-auth or OAuth2 password-grant username.",
    )
    p.add_argument(
        "--password-env",
        help="Environment variable holding the Basic-auth or OAuth2 password-grant password.",
    )
    p.add_argument(
        "--api-key-env",
        help="Environment variable holding an API key.",
    )
    p.add_argument(
        "--auth-header",
        help="Header name for --auth-type apikey/header (default X-API-Key / Authorization).",
    )
    p.add_argument(
        "--token-url",
        help="OAuth2 token endpoint. Fetches an access token for --check-api / --ping-servers.",
    )
    p.add_argument(
        "--auth-grant",
        choices=["client_credentials", "password"],
        help="OAuth2 grant used with --token-url (default: client_credentials, or password if username_env is set).",
    )
    p.add_argument(
        "--client-id-env",
        help="Environment variable holding the OAuth2 client id.",
    )
    p.add_argument(
        "--client-secret-env",
        help="Environment variable holding the OAuth2 client secret.",
    )
    p.add_argument(
        "--scope",
        help="OAuth2 scope string sent to the token endpoint.",
    )
    p.add_argument(
        "--probe-path",
        help="Path used by --check-api (e.g. /health). Default: /",
    )
    p.add_argument(
        "--embed-auth", action="store_true",
        help="Pre-authorize Swagger UI HTML with resolved credentials (do not publish the HTML).",
    )
    p.add_argument(
        "--no-embed-auth", action="store_true",
        help="Never write credentials into generated HTML (default).",
    )
    p.add_argument(
        "--check-api", action="store_true",
        help="GET the probe path on each configured host using the configured auth.",
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
        "--serve", action="store_true",
        help=(
            "After generating, serve Swagger UI on localhost and proxy Try-it-out "
            "to configured hosts (avoids browser CORS)."
        ),
    )
    p.add_argument(
        "--port", type=int, default=8765,
        help="Port for --serve (default 8765).",
    )
    p.add_argument(
        "--bind", default="127.0.0.1",
        help="Bind address for --serve (default 127.0.0.1).",
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


def _cli_auth(args):
    embed = True if args.embed_auth else False
    if args.no_embed_auth:
        embed = False
    return auth_from_cli(
        getattr(args, "auth_type", None),
        token=getattr(args, "token", None),
        token_env=getattr(args, "token_env", None),
        username_env=getattr(args, "username_env", None),
        password_env=getattr(args, "password_env", None),
        api_key_env=getattr(args, "api_key_env", None),
        header=getattr(args, "auth_header", None),
        probe_path=getattr(args, "probe_path", None),
        embed=embed if (args.embed_auth or args.no_embed_auth or getattr(args, "auth_type", None)) else None,
        token_url=getattr(args, "token_url", None),
        grant=getattr(args, "auth_grant", None),
        client_id_env=getattr(args, "client_id_env", None),
        client_secret_env=getattr(args, "client_secret_env", None),
        scope=getattr(args, "scope", None),
    )


def _apply_git_cli(repo: RepoConfig, args) -> None:
    if getattr(args, "git_token_env", None):
        repo.git_token_env = args.git_token_env
    if getattr(args, "git_username_env", None):
        repo.git_username_env = args.git_username_env
    if getattr(args, "git_password_env", None):
        repo.git_password_env = args.git_password_env


def _config_from_args(args) -> Config:
    cli_servers = _cli_servers(args)
    cli_auth = _cli_auth(args)
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
            if cli_auth:
                repo.auth = cli_auth
            _apply_git_cli(repo, args)
            repos.append(repo)
        config = Config(output=OutputConfig(), repos=repos)
    else:
        config = load_config(args.config)
        if cli_servers:
            for repo in config.repos:
                repo.servers = merge_servers(cli_servers, repo.servers)
        if cli_auth:
            for repo in config.repos:
                repo.auth = cli_auth
        for repo in config.repos:
            _apply_git_cli(repo, args)
    if args.embed_auth:
        for repo in config.repos:
            if repo.auth:
                repo.auth.embed = True
    if args.no_embed_auth:
        for repo in config.repos:
            if repo.auth:
                repo.auth.embed = False
    if args.probe_path:
        for repo in config.repos:
            if repo.auth:
                repo.auth.probe_path = args.probe_path
            elif args.check_api:
                from .auth import AuthConfig
                repo.auth = AuthConfig(probe_path=args.probe_path)
    if args.output:
        config.output.directory = args.output
    if args.formats:
        config.output.formats = args.formats
    if args.serve and "html" not in config.output.formats:
        config.output.formats = list(config.output.formats) + ["html"]
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
        if repo.auth and repo.auth.normalized_type():
            print(f"         auth: {describe_auth(repo.auth)}")
            if not has_credentials(repo.auth):
                print(
                    f"         warning: {missing_credential_hint(repo.auth)} "
                    "for live checks / HTML pre-authorize. "
                    "Swagger UI Authorize can still be used by hand.",
                    file=sys.stderr,
                )
            if repo.auth.embed and has_credentials(repo.auth):
                print(
                    "         warning: credentials were embedded in the HTML. "
                    "Do not publish swagger-output/*.html.",
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

        auth_headers = request_headers(repo.auth)

        if args.ping_servers:
            # Only operator-configured hosts — never URLs imported from a spec.
            ping_targets = list(repo.servers)
            if not ping_targets:
                print("         ping: skipped (no host configured)")
            for server in ping_targets:
                ok, detail = ping_url(server.url, extra_headers=auth_headers or None)
                flag = "ok" if ok else "fail"
                extra = " (auth sent)" if auth_headers else ""
                print(f"         ping [{flag}]: {detail}{extra}")
                if not ok:
                    validation_failed = True

        if args.check_api:
            probe = (repo.auth.probe_path if repo.auth else "") or "/"
            hosts = list(repo.servers)
            if not hosts:
                print("         check-api: skipped (no host configured)")
            elif repo.auth and repo.auth.normalized_type() and not auth_headers:
                hint = repo.auth._token_error or missing_credential_hint(repo.auth)
                print(f"         check-api: skipped ({hint})", file=sys.stderr)
                validation_failed = True
            for server in hosts:
                target = join_host_path(server.url, probe)
                ok, detail = ping_url(
                    target, extra_headers=auth_headers or None, method="GET"
                )
                flag = "ok" if ok else "fail"
                extra = " (auth sent)" if auth_headers else ""
                print(f"         check-api [{flag}]: {detail}{extra}")
                # 401/403 after sending auth usually means bad/missing credentials.
                if not ok or (
                    auth_headers
                    and detail.split(" ", 1)[0] in {"401", "403"}
                ):
                    validation_failed = True

        written = {}
        prefill = swagger_prefill(repo.auth, spec)
        if config.output.per_repo:
            written = write_spec(
                doc, out_dir, spec.name, config.output.formats, auth_prefill=prefill
            )

        sid = spec_slug(spec.name)
        index_entries.append(
            {
                "id": sid,
                "name": spec.name,
                "title": spec.title,
                "version": spec.version,
                "endpoint_count": endpoint_count,
                "frameworks": spec.detected_frameworks,
                "html": written["html"].name if "html" in written else None,
                "spec_file": f"{sid}.openapi.json"
                if "json" in config.output.formats
                else "",
                "servers": [s.url for s in spec.servers],
                "auth": describe_auth(repo.auth),
                "operations": operations_for_spec(spec),
                "spec": doc,
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
        index_path = write_portal(out_dir, index_entries)
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
    if args.serve:
        from .serve import serve_docs

        bind = getattr(args, "bind", "127.0.0.1") or "127.0.0.1"
        if bind not in {"127.0.0.1", "localhost", "::1"}:
            print(
                "warning: --bind is not loopback; the Try-it-out proxy will be "
                "reachable on this interface. Prefer 127.0.0.1.",
                file=sys.stderr,
            )
        serve_docs(out_dir, config, host=bind, port=int(args.port or 8765))
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
        label = spec.title or spec.name
        for ep in spec.endpoints:
            clone = copy.deepcopy(ep)
            clone.tags = [label]
            # Path-level servers so Try-it-out hits this service's deployed host.
            if spec.servers and not clone.servers:
                clone.servers = list(spec.servers)
            merged.add_endpoint(clone)
        fw = ", ".join(spec.detected_frameworks)
        host = spec.servers[0].url if spec.servers else ""
        merged.tag_meta[pretty_tag(label)] = " · ".join(
            p for p in (fw, host) if p
        )
        for name, scheme in spec.security_schemes.items():
            merged.add_security_scheme(scheme)
        for name, schema in spec.schemas.items():
            merged.schemas.setdefault(name, schema)
        for note in spec.notes:
            merged.add_note(f"[{spec.name}] {note}")
    return build_openapi(merged)


if __name__ == "__main__":
    raise SystemExit(main())
