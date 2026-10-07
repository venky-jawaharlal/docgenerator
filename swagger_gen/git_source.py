"""Resolve a repository from a local path or a remote git URL.

Remote sources are shallow-cloned into a local cache so the rest of the
pipeline can scan files as usual. ``git`` must be on PATH.

Clones never run repository hooks, never create checkouts with symlinks, and
reject option-like URLs/refs so git argv cannot be steered.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

from .servers import redact_url

_GIT_URL_RE = re.compile(
    r"""^(
        (?:git\+)?(?:https?|ssh|file):// |
        git@ |
        (?:github|gitlab|bitbucket)\.com[/:]
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Refs passed to ``git clone --branch`` / ``git checkout``. No leading dash
# (would be parsed as a git option) and no path traversal.
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")

_UNSAFE_GIT_ENV = {
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_SSH_COMMAND",
    "GIT_PROXY_COMMAND",
    "GIT_EXTERNAL_DIFF",
    "GIT_DIFF_OPTS",
    "GIT_EDITOR",
    "GIT_SEQUENCE_EDITOR",
    "GIT_PAGER",
    "GIT_TEMPLATE_DIR",
    "GIT_EXEC_PATH",
    "GIT_NAMESPACE",
    "GIT_CONFIG",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_TRACE",
    "GIT_TRACE2",
    "GIT_TRACE2_EVENT",
}

_CLONE_TIMEOUT_SEC = 180
_FETCH_TIMEOUT_SEC = 120

# These hosts speak HTTPS. Do not send a token to their HTTP port.
_PUBLIC_HTTPS_HOSTS = frozenset(
    {
        "github.com",
        "www.github.com",
        "gitlab.com",
        "www.gitlab.com",
        "bitbucket.org",
        "www.bitbucket.org",
    }
)


class SourceError(Exception):
    pass


def looks_like_git_url(value: str) -> bool:
    raw = (value or "").strip()
    if not raw or raw.startswith("-") or os.path.isdir(os.path.expanduser(raw)):
        return False
    if raw.lower().startswith("git://"):
        return False
    if raw.endswith(".git"):
        return True
    return bool(_GIT_URL_RE.match(raw))


def repo_name_from_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    # git@host:org/repo  or  ssh://git@host/org/repo
    if "://" not in cleaned and ":" in cleaned:
        cleaned = cleaned.split(":", 1)[-1]
    else:
        cleaned = re.sub(r"^[a-z+]+://[^/]+/", "", cleaned, flags=re.I)
    name = cleaned.rstrip("/").split("/")[-1]
    return name or "repo"


def _normalize_git_url(url: str) -> str:
    raw = (url or "").strip()
    if raw.startswith("git+") and "://" in raw:
        raw = raw[4:]
    if raw.startswith(("github.com/", "gitlab.com/", "bitbucket.org/")):
        raw = "https://" + raw
    return raw


def _assert_safe_git_url(url: str) -> None:
    raw = (url or "").strip()
    if not raw or raw.startswith("-") or "\n" in raw or "\x00" in raw:
        raise SourceError("Invalid git URL.")
    if raw.lower().startswith("git://"):
        raise SourceError(
            "The unencrypted git:// protocol is not allowed. Use https or ssh."
        )
    if not looks_like_git_url(raw) and not raw.lower().startswith(
        ("https://", "http://", "ssh://", "file://", "git@")
    ):
        raise SourceError(f"Unsupported git URL: {redact_url(raw)}")


def _assert_safe_ref(ref: str) -> None:
    if not ref:
        return
    if (
        ref.startswith("-")
        or ".." in ref
        or ref.startswith("/")
        or "//" in ref
        or "\n" in ref
        or "\x00" in ref
        or not _REF_RE.fullmatch(ref)
    ):
        raise SourceError(f"Invalid git ref: {ref!r}")


def _git_available() -> bool:
    return shutil.which("git") is not None


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in _UNSAFE_GIT_ENV:
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["GIT_PROTOCOL_FROM_USER"] = "0"
    return env


_ASKPASS_SCRIPT = """#!/usr/bin/env python3
import os
import sys
prompt = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
if "username" in prompt:
    sys.stdout.write(os.environ.get("SWAGGER_GEN_GIT_USER", ""))
else:
    sys.stdout.write(os.environ.get("SWAGGER_GEN_GIT_PASS", ""))
"""


def _ensure_askpass(cache_root: Path) -> Path:
    path = cache_root / ".git-askpass"
    if not path.is_file() or path.read_text(encoding="utf-8") != _ASKPASS_SCRIPT:
        path.write_text(_ASKPASS_SCRIPT, encoding="utf-8")
    path.chmod(stat.S_IRWXU)
    return path


def strip_git_userinfo(url: str) -> tuple[str, str, str]:
    """Return (url without userinfo, username, password).

    ``https://<token>@host/repo.git`` treats the token as the password.
    """
    raw = (url or "").strip()
    if "://" not in raw:
        return raw, "", ""
    parsed = urlparse(raw)
    if not (parsed.username or parsed.password):
        return raw, "", ""
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if user and not password:
        password = user
        user = "x-access-token"
    host = parsed.hostname or ""
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    clean = urlunparse(parsed._replace(netloc=netloc))
    return clean, user, password


def _git_secret(direct: str, env_name: str) -> str:
    """Resolve a clone secret from a value or an environment variable.

    ``token_env`` is normally the variable name. If that variable is unset and
    the field is not an ``ENV_NAME`` (for example a pasted ``ghp_`` or
    ``glpat-`` token), the field itself is the secret.
    """
    from .auth import _from_env

    got = _from_env(direct, env_name).strip()
    if got:
        return got
    env_name = (env_name or "").strip()
    if env_name and not re.fullmatch(r"[A-Z][A-Z0-9_]*", env_name):
        return env_name
    return ""


def resolve_git_auth(
    *,
    token: str = "",
    token_env: str = "",
    username: str = "",
    username_env: str = "",
    password: str = "",
    password_env: str = "",
) -> tuple[str, str]:
    """Resolve HTTPS clone credentials. Empty password means use git's own helper."""
    from .auth import _from_env

    secret = _git_secret(password, password_env) or _git_secret(token, token_env)
    user = _from_env(username, username_env).strip()
    if secret and not user:
        user = "x-access-token"
    return user, secret


# Prints username/password from the environment. The secret is not part of the command.
_CREDENTIAL_HELPER = (
    "!f() { test \"$1\" = get || exit 0; "
    "printf 'username=%s\\npassword=%s\\n' "
    "\"$SWAGGER_GEN_GIT_USER\" \"$SWAGGER_GEN_GIT_PASS\"; }; f"
)


def _run_git(
    args: list[str],
    cwd: Path | None = None,
    timeout: int = _FETCH_TIMEOUT_SEC,
    *,
    username: str = "",
    password: str = "",
    askpass: Path | None = None,
    bearer: str = "",
) -> subprocess.CompletedProcess:
    env = _git_env()
    # Never run repo hooks; never materialize symlinks from untrusted trees.
    # Empty credential.helper disables osxkeychain / manager prompts.
    cmd = [
        "git",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.symlinks=false",
        "-c", "credential.helper=",
    ]
    if password or bearer:
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "Never"
        # A global insteadOf that rewrites https to ssh would ignore the token.
        # The longest insteadOf wins, so pin each http(s) URL to itself.
        for arg in args:
            if arg.startswith(("http://", "https://")) and "=" not in arg and " " not in arg:
                cmd.extend(["-c", f"url.{arg}.insteadof={arg}"])
    if bearer:
        header = f"Authorization: Bearer {bearer}"
    elif password:
        # Send Basic on the first request. GitLab often answers 401 without a
        # challenge that would make the credential helper run.
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        header = f"Authorization: Basic {token}"
    else:
        header = ""
    if header:
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = header
    cmd.extend(args)
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return subprocess.CompletedProcess(
            cmd, 124, stdout, stderr or f"git timed out after {timeout}s"
        )


def _note_fallback(
    original: str, used: str, username: str, used_user: str, bearer: bool = False
) -> None:
    if used == original and used_user == username and not bearer:
        return
    bits = []
    if used != original:
        bits.append(redact_url(used))
        if urlparse(used).scheme == "http" and urlparse(original).scheme != "http":
            bits[-1] += " (token sent unencrypted)"
    if bearer:
        bits.append("a bearer token")
    elif used_user != username:
        bits.append(f"username {used_user}")
    if not bits:
        return
    print(
        f"warning: {redact_url(original)} did not accept the first login; "
        f"cloned with {' and '.join(bits)}.",
        file=sys.stderr,
    )


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _cache_dir_for(url: str, ref: str | None, cache_root: Path) -> Path:
    key = f"{url}|{ref or ''}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_name_from_url(url)).strip("-") or "repo"
    return cache_root / f"{slug}-{digest}"


def alternate_http_url(url: str) -> str | None:
    """Return the same URL with http and https swapped.

    Public forges are not downgraded from https to http, so a token is not
    sent in the clear to github.com, gitlab.com, or bitbucket.org.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme == "https":
        other = "http"
    elif scheme == "http":
        other = "https"
    else:
        return None
    host = (parsed.hostname or "").lower()
    if other == "http" and host in _PUBLIC_HTTPS_HOSTS:
        return None
    return urlunparse(parsed._replace(scheme=other))


def _auth_rejected(text: str) -> bool:
    blob = (text or "").lower()
    return any(
        phrase in blob
        for phrase in (
            "http basic: access denied",
            "authentication failed",
            "invalid username or token",
            "invalid username or password",
            "could not read username",
            "support for password authentication was removed",
            "the requested url returned error: 401",
            "the requested url returned error: 403",
        )
    )


def _transport_failed(text: str) -> bool:
    blob = (text or "").lower()
    return any(
        phrase in blob
        for phrase in (
            "could not resolve host",
            "connection refused",
            "connection timed out",
            "failed to connect",
            "ssl certificate",
            "ssl_error",
            "tls",
            "wrong version number",
            "server certificate",
            "error:1404",
        )
    )


def _ssh_to_https(url: str) -> str | None:
    """Turn ``git@host:path`` or ``ssh://git@host/path`` into an https URL."""
    raw = (url or "").strip()
    if raw.startswith("ssh://"):
        parsed = urlparse(raw)
        host = parsed.hostname or ""
        path = parsed.path or ""
        if not host or not path:
            return None
        return f"https://{host}{path if path.startswith('/') else '/' + path}"
    if raw.startswith("git@") and ":" in raw[4:]:
        host, path = raw[4:].split(":", 1)
        host = host.strip()
        path = path.strip().lstrip("/")
        if host and path and " " not in host:
            return f"https://{host}/{path}"
    return None


def _http_urls(url: str) -> list[str]:
    """http(s) URLs to try. SSH clone URLs become https, then http when allowed."""
    raw = (url or "").strip()
    urls: list[str] = []

    def add(candidate: str | None) -> None:
        if candidate and candidate not in urls:
            urls.append(candidate)

    scheme = urlparse(raw).scheme.lower() if "://" in raw else ""
    if scheme in ("http", "https"):
        add(raw)
        add(alternate_http_url(raw))
        return urls
    https = _ssh_to_https(raw)
    if https:
        add(https)
        add(alternate_http_url(https))
        return urls
    add(raw)
    return urls


def _looks_like_gitlab(url: str) -> bool:
    parts = [url or ""]
    https = _ssh_to_https(url)
    if https:
        parts.append(https)
    blob = " ".join(parts).lower()
    return "gitlab" in blob


def _clone_attempts(
    url: str, username: str, password: str, username_explicit: bool
) -> list[tuple[str, str, bool]]:
    """``(url, username, bearer)`` attempts, configured login first.

    GitLab personal, project, and group access tokens authenticate as
    ``oauth2`` (deploy tokens keep their own username; CI job tokens use
    ``gitlab-ci-token``). GitHub accepts ``x-access-token``. Those fallbacks
    run after the configured username is rejected. SSH URLs are cloned over
    https when a token or password is set, because SSH cannot use that secret.
    """
    if not password:
        return [(url, username, False)]
    gitlab = _looks_like_gitlab(url)
    # The GitHub default username is rejected by GitLab before oauth2 is tried.
    if gitlab and not username_explicit and username == "x-access-token":
        username = ""
    preferred = ["oauth2", "gitlab-ci-token"] if gitlab else ["x-access-token", "oauth2"]
    users: list[str] = []
    for candidate in (username, *preferred, "x-access-token", "git"):
        if candidate and candidate not in users:
            users.append(candidate)
    urls = _http_urls(url)
    attempts = [(candidate, user, False) for candidate in urls for user in users]
    for candidate in urls:
        attempts.append((candidate, users[0], True))
    return attempts


def _likely_auth_miss(text: str) -> bool:
    """Private hosts often say 'not found' when the credentials were rejected."""
    blob = (text or "").lower()
    return any(
        phrase in blob
        for phrase in (
            "repository not found",
            "project you were looking for could not be found",
            "incorrect username or password",
            "permission denied (publickey)",
            "could not read from remote repository",
        )
    )


def _retryable(text: str) -> bool:
    return _auth_rejected(text) or _transport_failed(text) or _likely_auth_miss(text)


def _sanitize_git_output(text: str, url: str, *secrets: str) -> str:
    redacted = redact_url(url)
    blob = (text or "").strip()
    if url:
        blob = blob.replace(url, redacted)
    for secret in secrets:
        if secret and len(secret) >= 4:
            blob = blob.replace(secret, "****")
    # Also strip any remaining user:password@ patterns.
    blob = re.sub(r"://[^/\s:@]+:[^/\s@]+@", "://****@", blob)
    blob = re.sub(
        r"(?i)(authorization:\s*(?:basic|bearer)\s+)\S+",
        r"\1****",
        blob,
    )
    return blob[:2000]


def clone_or_update(
    url: str,
    ref: str | None,
    cache_root: Path,
    *,
    username: str = "",
    password: str = "",
    username_explicit: bool = False,
) -> Path:
    """Shallow-clone ``url`` (optional ``ref``) into ``cache_root`` and return the path.

    Reuses an existing clone of the same URL+ref when present. HTTPS credentials
    are supplied through a private askpass helper and are not written into the
    remote URL. SSH URLs still use the local SSH agent.
    """
    if not _git_available():
        raise SourceError(
            "git is required to clone remote repositories. "
            "Install git, or point the config at a local path instead."
        )
    url = _normalize_git_url(url)
    url, embedded_user, embedded_pass = strip_git_userinfo(url)
    if not password and embedded_pass:
        password = embedded_pass
        username = username or embedded_user
    _assert_safe_git_url(url)
    _assert_safe_ref(ref or "")

    cache_root.mkdir(parents=True, exist_ok=True)
    _chmod_private(cache_root)
    askpass = _ensure_askpass(cache_root) if password else None

    if password and not username and not username_explicit:
        username = "oauth2" if _looks_like_gitlab(url) else "x-access-token"

    dest = _cache_dir_for(url, ref, cache_root)
    dest.parent.mkdir(parents=True, exist_ok=True)

    def run(
        args: list[str],
        cwd: Path | None,
        timeout: int,
        user: str,
        secret: str,
        *,
        bearer: str = "",
    ):
        return _run_git(
            args,
            cwd=cwd,
            timeout=timeout,
            username=user,
            password="" if bearer else secret,
            askpass=None if bearer else askpass,
            bearer=bearer,
        )

    if (dest / ".git").is_dir():
        fetch_args = ["fetch", "--depth", "1", "origin"]
        if ref:
            fetch_args.append(ref)
        fetched = run(fetch_args, dest, _FETCH_TIMEOUT_SEC, username, password)
        if fetched.returncode == 0:
            target = ref or "FETCH_HEAD"
            checked = run(
                ["checkout", "--force", target], dest, _FETCH_TIMEOUT_SEC, username, password
            )
            if checked.returncode == 0:
                _chmod_private(dest)
                return dest
        shutil.rmtree(dest, ignore_errors=True)

    template = cache_root / ".empty-template"
    template.mkdir(parents=True, exist_ok=True)

    def attempt(try_url: str, try_user: str, use_bearer: bool) -> tuple[bool, str]:
        shutil.rmtree(dest, ignore_errors=True)
        bearer = password if use_bearer else ""

        def git(args: list[str], cwd: Path | None = None, timeout: int = _FETCH_TIMEOUT_SEC):
            return run(args, cwd, timeout, try_user, password, bearer=bearer)

        clone_args = ["clone", "--depth", "1", f"--template={template}"]
        if ref:
            clone_args += ["--branch", ref]
        clone_args += [try_url, str(dest)]
        cloned = git(clone_args, timeout=_CLONE_TIMEOUT_SEC)
        if cloned.returncode == 0:
            _chmod_private(dest)
            return True, ""

        # ``--branch`` only works for branches/tags. Retry without it for SHAs
        # when the failure is not an auth or connection problem.
        detail = _sanitize_git_output(
            cloned.stderr or cloned.stdout, try_url, try_user, password
        ) or "git clone failed"
        if ref and not _retryable(detail):
            shutil.rmtree(dest, ignore_errors=True)
            cloned = git(
                ["clone", f"--template={template}", try_url, str(dest)],
                timeout=_CLONE_TIMEOUT_SEC,
            )
            if cloned.returncode == 0:
                checked = git(["checkout", "--force", ref], cwd=dest)
                if checked.returncode == 0:
                    _chmod_private(dest)
                    return True, ""
                detail = _sanitize_git_output(
                    checked.stderr or checked.stdout, try_url, try_user, password
                ) or "could not check out the requested ref"
                return False, detail
            detail = _sanitize_git_output(
                cloned.stderr or cloned.stdout, try_url, try_user, password
            ) or detail
        return False, detail

    saw_auth = False
    saw_transport = False
    last_detail = "git clone failed"
    tried: list[str] = []
    attempts = _clone_attempts(url, username, password, username_explicit)
    primary_url, primary_user, _primary_bearer = attempts[0]
    for try_url, try_user, use_bearer in attempts:
        if use_bearer and not saw_auth:
            continue
        if try_user != primary_user and not use_bearer and not saw_auth:
            continue
        if try_url != primary_url and not (saw_auth or saw_transport):
            continue
        label = redact_url(try_url)
        if use_bearer:
            label += " with bearer token"
        elif try_user and try_user != primary_user:
            label += f" as {try_user}"
        tried.append(label)
        ok, detail = attempt(try_url, try_user, use_bearer)
        if ok:
            _note_fallback(url, try_url, primary_user, try_user, use_bearer)
            return dest
        last_detail = detail or last_detail
        if _auth_rejected(detail) or _likely_auth_miss(detail):
            saw_auth = True
        elif _transport_failed(detail):
            saw_transport = True
        else:
            break

    message = f"Failed to clone {redact_url(url)}: {last_detail}"
    if len(tried) > 1:
        message += " Also tried " + ", ".join(tried[1:]) + "."
    if saw_auth:
        message += (
            " GitHub tokens use username x-access-token."
            " GitLab personal, project, and group tokens use username oauth2"
            " and need the read_repository scope; deploy tokens use the"
            " username GitLab shows next to the token."
            " Bitbucket needs your account username with the app password."
        )
    raise SourceError(message)
