"""Resolve a repository from a local path or a remote git URL.

Remote sources are shallow-cloned into a local cache so the rest of the
pipeline can scan files as usual. ``git`` must be on PATH.

Clones never run repository hooks, never create checkouts with symlinks, and
reject option-like URLs/refs so git argv cannot be steered.
"""

from __future__ import annotations

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

    secret = _from_env(password, password_env) or _from_env(token, token_env)
    user = _from_env(username, username_env)
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
    if password:
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "Never"
        env["SWAGGER_GEN_GIT_USER"] = username
        env["SWAGGER_GEN_GIT_PASS"] = password
        if askpass is not None:
            env["GIT_ASKPASS"] = str(askpass)
            env["SSH_ASKPASS"] = str(askpass)
            env["SSH_ASKPASS_REQUIRE"] = "force"
        cmd.extend(["-c", f"credential.helper={_CREDENTIAL_HELPER}"])
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


def _note_fallback(original: str, used: str, username: str, used_user: str) -> None:
    if used == original and used_user == username:
        return
    bits = []
    if used != original:
        bits.append(redact_url(used))
        if urlparse(used).scheme == "http" and urlparse(original).scheme == "https":
            bits[-1] += " (token sent unencrypted)"
    if used_user != username:
        bits.append(f"username {used_user}")
    print(
        f"warning: {redact_url(original)} did not accept the credentials; "
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


def _clone_attempts(
    url: str, username: str, password: str, username_explicit: bool
) -> list[tuple[str, str]]:
    """URLs and usernames to try, original first.

    A token with no username is sent as ``x-access-token`` (GitHub). GitLab
    rejects that with HTTP Basic: Access denied and expects ``oauth2``. Both
    http and https are listed so an enterprise host on the other port is tried
    only after the configured URL fails.
    """
    users = [username]
    if password and not username_explicit:
        for extra in ("oauth2", "git"):
            if extra not in users:
                users.append(extra)
    urls = [url]
    other = alternate_http_url(url)
    if other:
        urls.append(other)
    return [(candidate, user) for candidate in urls for user in users]


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
        username = "x-access-token"

    dest = _cache_dir_for(url, ref, cache_root)
    dest.parent.mkdir(parents=True, exist_ok=True)

    def run(
        args: list[str],
        cwd: Path | None,
        timeout: int,
        user: str,
        secret: str,
    ):
        return _run_git(
            args,
            cwd=cwd,
            timeout=timeout,
            username=user,
            password=secret,
            askpass=askpass,
        )

    if (dest / ".git").is_dir():
        fetch_args = ["fetch", "--depth", "1", "origin"]
        if ref:
            fetch_args.append(ref)
        fetched = run(fetch_args, dest, _FETCH_TIMEOUT_SEC, username, password)
        if fetched.returncode == 0:
            target = ref or "FETCH_HEAD"
            checked = run(["checkout", "--force", target], dest, _FETCH_TIMEOUT_SEC, username, password)
            if checked.returncode == 0:
                _chmod_private(dest)
                return dest
        shutil.rmtree(dest, ignore_errors=True)

    template = cache_root / ".empty-template"
    template.mkdir(parents=True, exist_ok=True)

    def attempt(try_url: str, try_user: str) -> tuple[bool, str]:
        shutil.rmtree(dest, ignore_errors=True)

        def git(args: list[str], cwd: Path | None = None, timeout: int = _FETCH_TIMEOUT_SEC):
            return run(args, cwd, timeout, try_user, password)

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
        if ref and not _auth_rejected(detail) and not _transport_failed(detail):
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
    for try_url, try_user in _clone_attempts(url, username, password, username_explicit):
        if try_user != username and not saw_auth:
            continue
        if try_url != url and not (saw_auth or saw_transport):
            continue
        label = redact_url(try_url)
        if try_user and try_user != username:
            label += f" as {try_user}"
        tried.append(label)
        ok, detail = attempt(try_url, try_user)
        if ok:
            _note_fallback(url, try_url, username, try_user)
            return dest
        last_detail = detail or last_detail
        if _auth_rejected(detail):
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
            " GitLab tokens need username oauth2."
            " Bitbucket needs your account username with the app password."
        )
    raise SourceError(message)
