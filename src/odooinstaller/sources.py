"""Downloading Odoo source code, including the private Enterprise repository."""

from __future__ import annotations

import base64
import json
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .system import Runner
from .versions import ENTERPRISE_REPO, ODOO_REPO

ENTERPRISE_API = "https://api.github.com/repos/odoo/enterprise"


@dataclass(frozen=True)
class AccessResult:
    ok: bool
    message: str
    needs_auth: bool = False


def check_enterprise_access(token: str, *, timeout: float = 15) -> AccessResult:
    """Ask GitHub whether ``token`` can read odoo/enterprise, before a long clone fails."""
    token = token.strip()
    if not token:
        return AccessResult(False, "no token given")
    request = urllib.request.Request(
        ENTERPRISE_API,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "odooinstaller",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        reasons = {
            401: "the token is invalid or expired",
            403: "GitHub refused the request (token lacks permission or rate limit hit)",
            404: "this token's GitHub account has no access to odoo/enterprise",
        }
        return AccessResult(False, reasons.get(exc.code, f"GitHub answered HTTP {exc.code}"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return AccessResult(False, f"could not reach GitHub ({exc})")
    if data.get("full_name", "").lower() != "odoo/enterprise":
        return AccessResult(False, "unexpected answer from GitHub")
    return AccessResult(True, "access to odoo/enterprise confirmed")


# Never wait for input: no password prompts, no SSH host-key questions.
NON_INTERACTIVE = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
}


def git_auth_env(token: str, url: str = "https://github.com/") -> dict[str, str]:
    """Pass the token to git through environment config, never the URL or command line.

    That keeps it out of ``ps`` output, ``.git/config`` remotes and our log files.
    GitHub expects the user name ``x-access-token``; GitLab expects ``oauth2``.
    """
    parsed = urlparse(url)
    host = parsed.netloc or "github.com"
    user = "x-access-token" if host.endswith("github.com") else "oauth2"
    basic = base64.b64encode(f"{user}:{token.strip()}".encode()).decode()
    return {
        **NON_INTERACTIVE,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.https://{host}/.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


_AUTH_ERRORS = (
    "authentication failed",
    "could not read username",
    "repository not found",
    "permission denied",
    "access denied",
    "403",
    "401",
)


def check_repo(runner: Runner, url: str, branch: str, token: str | None = None) -> AccessResult:
    """Check that ``url`` is reachable and has ``branch``, without downloading it."""
    env = git_auth_env(token, url) if token and url.startswith("https://") else dict(NON_INTERACTIVE)
    result = runner.run(["git", "ls-remote", "--heads", url, branch], env=env, check=False)
    output = result.stdout.lower()
    if result.returncode != 0:
        if any(marker in output for marker in _AUTH_ERRORS):
            return AccessResult(False, "repository not found or access denied (private?)", needs_auth=True)
        last = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "unknown error"
        return AccessResult(False, f"cannot reach repository: {last}")
    if f"refs/heads/{branch}" not in result.stdout:
        return AccessResult(False, f"branch {branch!r} does not exist in this repository")
    return AccessResult(True, f"found branch {branch}")


# Only the latest commit of one branch: about 10x smaller and faster than a full clone.
SHALLOW = ["--depth", "1", "--single-branch"]


def is_complete_clone(runner: Runner, dest: Path, branch: str) -> bool:
    """True if ``dest`` holds a usable checkout of ``branch`` (not a half-finished download)."""
    if not (dest / ".git").exists():
        return False
    head = runner.run(["git", "-C", str(dest), "rev-parse", "--abbrev-ref", "HEAD"], check=False)
    return head.returncode == 0 and head.stdout.strip() == branch


def clone(runner: Runner, repo: str, branch: str, dest: Path, *, env: dict[str, str] | None = None) -> bool:
    """Shallow-clone one branch. Returns False if ``dest`` already had the code."""
    if is_complete_clone(runner, dest, branch) and not runner.dry_run:
        return False
    if dest.exists() and not runner.dry_run:
        # Left behind by an interrupted download (e.g. Ctrl+C): start again cleanly.
        shutil.rmtree(dest)
    runner.mkdir(dest.parent)
    runner.run(
        [
            "git",
            "clone",
            "--progress",
            *SHALLOW,
            "--branch",
            branch,
            repo,
            str(dest),
        ],
        env={**NON_INTERACTIVE, **(env or {})},
    )
    return True


def update(runner: Runner, dest: Path, branch: str, *, env: dict[str, str] | None = None) -> None:
    git_env = {**NON_INTERACTIVE, **(env or {})}
    git = ["git", "-C", str(dest)]
    runner.run([*git, "fetch", "--progress", "--depth", "1", "origin", branch], env=git_env)
    runner.run([*git, "reset", "--hard", "FETCH_HEAD"])
    # Drop the previous commit's objects so repeated updates don't grow the folder.
    runner.run([*git, "reflog", "expire", "--expire=now", "--all"])
    runner.run([*git, "gc", "--prune=now", "--quiet"])


def clone_community(runner: Runner, version: str, dest: Path) -> bool:
    return clone(runner, ODOO_REPO, version, dest)


def clone_enterprise(runner: Runner, version: str, dest: Path, token: str) -> bool:
    return clone(runner, ENTERPRISE_REPO, version, dest, env=git_auth_env(token))
