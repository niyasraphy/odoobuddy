"""Running commands and inspecting the machine."""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from .models import Mode


class CommandError(RuntimeError):
    """A command failed. ``output`` holds the tail of what it printed."""

    def __init__(self, cmd: Sequence[str], returncode: int, output: str) -> None:
        self.cmd = list(cmd)
        self.returncode = returncode
        self.output = output
        super().__init__(f"command failed ({returncode}): {shlex.join(self.cmd)}")


def short_paths(text: str) -> str:
    """Show the home folder as ~ so dry-run output stays readable."""
    home = str(Path.home())
    return text.replace(home + "/", "~/") if home != "/" else text


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
    # Called with each line a command prints, so the UI can show live progress.
    on_output: Callable[[str], None] | None = None
    # Set by SudoSession: sudo must never stop to ask for a password behind a spinner.
    sudo_noninteractive: bool = False

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
            self.console.print(f"  [dim]$ {escape(short_paths(shlex.join(full)))}[/dim]")
            return subprocess.CompletedProcess(full, 0, "", "")
        self._log(f"$ {shlex.join(full)}")
        run_env = {**os.environ, **env} if env else None
        proc = subprocess.Popen(
            full,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=run_env,
            cwd=cwd,
            text=True,
            bufsize=1,
        )
        if input is not None:
            assert proc.stdin is not None
            proc.stdin.write(input)
            proc.stdin.close()
        tail: deque[str] = deque(maxlen=25)
        collected: list[str] = []
        assert proc.stdout is not None
        # Text mode turns progress-bar carriage returns into separate lines.
        for line in proc.stdout:
            line = line.rstrip("\n")
            self._log(line)
            collected.append(line)
            tail.append(line)
            if self.on_output and line.strip():
                self.on_output(line.strip())
        returncode = proc.wait()
        output = "\n".join(collected)
        if check and returncode != 0:
            raise CommandError(full, returncode, "\n".join(tail))
        return subprocess.CompletedProcess(full, returncode, output, "")

    def write_file(self, path: Path, content: str, *, mode: int = 0o644, owner: str | None = None) -> None:
        if self.dry_run:
            self.console.print(f"  [dim]write {escape(short_paths(str(path)))}[/dim]")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)
        if owner:
            shutil.chown(path, owner, owner)
        self._log(f"wrote {path}")

    def mkdir(self, path: Path, *, owner: str | None = None, mode: int = 0o755) -> None:
        if self.dry_run:
            self.console.print(f"  [dim]mkdir {escape(short_paths(str(path)))}[/dim]")
            return
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
        if owner:
            shutil.chown(path, owner, owner)

    def _prefix(self, *, sudo: bool, as_user: str | None) -> list[str]:
        sudo_cmd = ["sudo", "-n"] if self.sudo_noninteractive else ["sudo"]
        if as_user:
            # runuser ships with util-linux, so it works on minimal servers without sudo.
            return ["runuser", "-u", as_user, "--"] if self.root else [*sudo_cmd, "-u", as_user, "-H", "--"]
        if sudo and not self.root:
            return sudo_cmd
        return []

    def _log(self, text: str) -> None:
        if self.log_file and text:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(text if text.endswith("\n") else text + "\n")


class SudoSession:
    """Ask for the sudo password once, visibly, then keep it fresh during long operations.

    While active, the runner calls ``sudo -n`` so a command can never sit waiting for a
    password that the progress spinner would hide.
    """

    REFRESH_SECONDS = 60

    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> SudoSession:
        if self.runner.root or self.runner.dry_run:
            return self
        if subprocess.run(["sudo", "-n", "-v"], capture_output=True, check=False).returncode != 0:
            self.runner.console.print(
                "[bold]Administrator rights are needed[/bold] (system packages, PostgreSQL). "
                "Enter your password below; it is not shown while typing."
            )
            if subprocess.run(["sudo", "-v"], check=False).returncode != 0:
                raise PermissionError("sudo password was not accepted")
        self.runner.sudo_noninteractive = True
        self._thread = threading.Thread(target=self._refresh, daemon=True)
        self._thread.start()
        return self

    def _refresh(self) -> None:
        while not self._stop.wait(self.REFRESH_SECONDS):
            subprocess.run(["sudo", "-n", "-v"], capture_output=True, check=False)

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self.runner.sudo_noninteractive = False


def wait_for_port(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


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


def memory_available_mb(path: Path = Path("/proc/meminfo")) -> int:
    """RAM free for new programs (what running instances already use is not counted). 0 if unknown."""
    try:
        for line in path.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def free_disk_gb(path: Path) -> float:
    """Free space on the disk that ``path`` is (or will be) on."""
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free / 1024**3


def prune_old_logs(log_dir: Path, days: int) -> list[Path]:
    """Delete odoobuddy's own run logs (``*.log`` in ``log_dir``) older than ``days``."""
    cutoff = time.time() - days * 86400
    deleted = []
    try:
        for path in log_dir.glob("*.log"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                deleted.append(path)
    except OSError:
        pass  # never let tidying up stop the actual command
    return deleted


APT_PROCESSES = ("unattended-upgr", "apt", "apt-get", "dpkg")


def apt_busy(proc: Path = Path("/proc")) -> str | None:
    """Name of a package manager that is working right now (it holds apt's lock), or None."""
    try:
        for comm in proc.glob("[0-9]*/comm"):
            try:
                name = comm.read_text().strip()
                if name not in APT_PROCESSES:
                    continue
                # Always running on Ubuntu, waiting for shutdown: not installing anything.
                if "unattended-upgrade-shutdown" in (comm.parent / "cmdline").read_text():
                    continue
            except OSError:
                continue  # the process ended meanwhile
            return name
    except OSError:
        pass
    return None


def detect_mode() -> tuple[Mode, str]:
    """("server" or "local", why): a desktop session means a computer someone works at."""
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return "local", "desktop session found"
    if os.environ.get("SSH_CONNECTION"):
        return "server", "connected over SSH, no desktop"
    return "server", "no desktop session"


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


def public_ip(timeout: float = 5) -> str | None:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=timeout) as response:
            return response.read().decode().strip() or None
    except OSError:
        return None


def server_address() -> str | None:
    """The IP to open a server without a domain at: the public IP, else the outgoing interface's."""
    ip = public_ip()
    if ip:
        return ip
    try:
        # connect() on UDP sends nothing; it only picks the interface used to reach the internet.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return None
