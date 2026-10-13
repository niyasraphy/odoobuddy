"""Running commands and inspecting the machine."""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console


class CommandError(RuntimeError):
    """A command failed. ``output`` holds the tail of what it printed."""

    def __init__(self, cmd: Sequence[str], returncode: int, output: str) -> None:
        self.cmd = list(cmd)
        self.returncode = returncode
        self.output = output
        super().__init__(f"command failed ({returncode}): {shlex.join(self.cmd)}")


def is_root() -> bool:
    return os.geteuid() == 0


@dataclass
class Runner:
    """Runs commands, honouring --dry-run and writing full output to a log file.

    Environment variables passed with ``env`` are never printed or logged, which is how
    secrets such as the GitHub token are handed to git.
    """

    console: Console = field(default_factory=Console)
    dry_run: bool = False
    log_file: Path | None = None
    root: bool = field(default_factory=is_root)

    def run(
        self,
        cmd: Sequence[str],
        *,
        sudo: bool = False,
        as_user: str | None = None,
        input: str | None = None,
        env: Mapping[str, str] | None = None,
        cwd: Path | str | None = None,
        check: bool = True,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        full = self._prefix(sudo=sudo, as_user=as_user) + [str(c) for c in cmd]
        if self.dry_run:
            self.console.print(f"  [dim]$ {shlex.join(full)}[/dim]")
            return subprocess.CompletedProcess(full, 0, "", "")
        self._log(f"$ {shlex.join(full)}")
        run_env = {**os.environ, **env} if env else None
        result = subprocess.run(
            full,
            input=input,
            env=run_env,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self._log(result.stdout)
        if check and result.returncode != 0:
            tail = "\n".join(result.stdout.strip().splitlines()[-25:])
            raise CommandError(full, result.returncode, tail)
        return result

    def write_file(self, path: Path, content: str, *, mode: int = 0o644, owner: str | None = None) -> None:
        if self.dry_run:
            self.console.print(f"  [dim]write {path}[/dim]")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)
        if owner:
            shutil.chown(path, owner, owner)
        self._log(f"wrote {path}")

    def mkdir(self, path: Path, *, owner: str | None = None, mode: int = 0o755) -> None:
        if self.dry_run:
            self.console.print(f"  [dim]mkdir {path}[/dim]")
            return
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
        if owner:
            shutil.chown(path, owner, owner)

    def _prefix(self, *, sudo: bool, as_user: str | None) -> list[str]:
        if as_user:
            # runuser ships with util-linux, so it works on minimal servers without sudo.
            return ["runuser", "-u", as_user, "--"] if self.root else ["sudo", "-u", as_user, "-H", "--"]
        if sudo and not self.root:
            return ["sudo"]
        return []

    def _log(self, text: str) -> None:
        if self.log_file and text:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(text if text.endswith("\n") else text + "\n")


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return True
    return False


def os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return data
    for line in lines:
        key, sep, value = line.partition("=")
        if sep:
            data[key.strip()] = value.strip().strip('"')
    return data


def memory_mb(path: Path = Path("/proc/meminfo")) -> int:
    try:
        for line in path.read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def cpu_count() -> int:
    return os.cpu_count() or 1


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def ssh_port() -> int | None:
    """The port of the SSH session we are running in, so the firewall never locks us out."""
    parts = os.environ.get("SSH_CONNECTION", "").split()
    if len(parts) == 4 and parts[3].isdigit():
        return int(parts[3])
    return None
