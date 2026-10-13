"""Custom module repositories, cloned into an instance's custom/ folder."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from . import render
from .installer import find_uv
from .models import Instance
from .sources import NON_INTERACTIVE, git_auth_env
from .system import Runner

SKIP_DIRS = {"setup", "node_modules", "__pycache__", "venv", ".git"}
MAX_DEPTH = 3
_ADDONS_LINE = re.compile(r"^addons_path\s*=.*$", re.MULTILINE)


def repo_dir_name(url: str) -> str:
    """'git@github.com:OCA/web.git' -> 'web', 'https://host/acme/my-mods' -> 'my-mods'."""
    path = urlparse(url).path if "://" in url else url.split(":", 1)[-1]
    name = path.rstrip("/").rsplit("/", 1)[-1]
    name = name[:-4] if name.endswith(".git") else name
    if not name or name in {".", ".."} or name == "addons":
        raise ValueError(f"cannot work out a folder name from {url!r}, pass one with --dir")
    return name


def module_folders(repo: Path) -> list[Path]:
    """Folders that directly contain Odoo modules (what addons_path needs).

    Works for OCA-style repos (modules at the root) and for repos that keep modules in a
    subfolder such as ``addons/``. OCA's ``setup/`` folder of symlinks is skipped.
    """
    found: set[Path] = set()
    base_depth = len(repo.parts)
    for current, dirs, files in os.walk(repo, followlinks=False):
        here = Path(current)
        if "__manifest__.py" in files and here != repo:
            found.add(here.parent)
            dirs[:] = []  # don't look inside a module
            continue
        if len(here.parts) - base_depth >= MAX_DEPTH:
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
    return sorted(found)


def custom_addons_paths(custom_dir: Path) -> list[Path]:
    """custom/addons first, then the module folders of every cloned repository."""
    paths = [custom_dir / "addons"]
    if custom_dir.is_dir():
        for repo in sorted(p for p in custom_dir.iterdir() if p.is_dir() and p.name != "addons"):
            paths += [p for p in module_folders(repo) if p not in paths]
    return paths


@dataclass
class RepoInfo:
    name: str
    path: Path
    url: str
    branch: str
    commit: str


def list_repos(runner: Runner, instance: Instance) -> list[RepoInfo]:
    repos: list[RepoInfo] = []
    custom = instance.custom_dir
    if not custom.is_dir():
        return repos
    for path in sorted(p for p in custom.iterdir() if (p / ".git").exists()):

        def git(*args: str, path: Path = path) -> str:
            return runner.run(["git", "-C", str(path), *args], check=False).stdout.strip()

        repos.append(
            RepoInfo(
                name=path.name,
                path=path,
                url=git("remote", "get-url", "origin"),
                branch=git("rev-parse", "--abbrev-ref", "HEAD"),
                commit=git("log", "-1", "--format=%h %s"),
            )
        )
    return repos


def clone_repo(
    runner: Runner,
    instance: Instance,
    url: str,
    branch: str,
    *,
    token: str | None = None,
    folder: str | None = None,
) -> Path:
    """Clone a repository into custom/<folder>. Full history, so you can commit and push."""
    dest = instance.custom_dir / (folder or repo_dir_name(url))
    if dest.exists() and not runner.dry_run:
        raise FileExistsError(f"{dest} already exists")
    env = git_auth_env(token, url) if token and url.startswith("https://") else dict(NON_INTERACTIVE)
    runner.mkdir(instance.custom_dir)
    runner.run(["git", "clone", "--progress", "--branch", branch, url, str(dest)], env=env)
    return dest


def install_requirements(runner: Runner, instance: Instance, repo: Path) -> bool:
    """Install a repository's Python dependencies into the instance's venv, if it has any."""
    requirements = repo / "requirements.txt"
    if not requirements.is_file():
        return False
    runner.run([find_uv(), "pip", "install", "--python", str(instance.python_bin), "-r", str(requirements)])
    return True


def refresh_addons_path(runner: Runner, instance: Instance) -> list[Path]:
    """Rewrite only the addons_path line of odoo.conf, keeping the user's other edits."""
    paths = render.addons_path(instance)
    line = f"addons_path = {','.join(str(p) for p in paths)}"
    if runner.dry_run:
        runner.console.print(f"  [dim]update {instance.config_path}: {line}[/dim]")
        return paths
    text = instance.config_path.read_text(encoding="utf-8")
    if _ADDONS_LINE.search(text):
        text = _ADDONS_LINE.sub(lambda _: line, text, count=1)
    else:
        text = text.replace("[options]\n", f"[options]\n{line}\n", 1)
    instance.config_path.write_text(text, encoding="utf-8")
    return paths


def pull(runner: Runner, repo: Path) -> None:
    runner.run(["git", "-C", str(repo), "pull", "--ff-only"], env=dict(NON_INTERACTIVE))
