"""Copy a production database and filestore from a server to a local instance, neutralized.

Everything goes over SSH with the user's own keys and is streamed: nothing is left on the
server. The remote user must be root or have password-less sudo.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO, Any

from . import postgres
from .models import Instance
from .system import CommandError, Runner
from .versions import major

REMOTE_REGISTRY = "/etc/odoobuddy/instances.json"
CHUNK = 1024 * 1024
_DB_RE = re.compile(r"[A-Za-z0-9_.-]{1,63}")


class PullError(RuntimeError):
    pass


@dataclass(frozen=True)
class Source:
    host: str  # what ssh connects to: "user@host" or "host"
    instance: str | None  # odoobuddy instance name on the server, if any


def parse_source(text: str) -> Source:
    """'root@erp.example.com:shop' -> host + instance; 'root@erp.example.com' -> host only."""
    host, _, instance = text.partition(":")
    if not host or " " in text:
        raise PullError(f"expected USER@HOST:INSTANCE, got {text!r}")
    return Source(host=host, instance=instance or None)


def check_db_name(name: str) -> str:
    if not _DB_RE.fullmatch(name):
        raise PullError(f"invalid database name {name!r} (letters, digits, '_', '-', '.')")
    return name


def default_local_db(remote_db: str, today: date | None = None) -> str:
    return f"{remote_db}_{(today or date.today()):%Y%m%d}"


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract without letting the archive write outside ``dest``."""
    if hasattr(tarfile, "data_filter"):  # Python 3.10.12+ / 3.11.4+
        tar.extractall(dest, filter="data")
        return
    root = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not (member.isfile() or member.isdir()) or not target.is_relative_to(root):
            raise PullError(f"unexpected entry in filestore archive: {member.name}")
    tar.extractall(dest)


class Remote:
    """Runs commands on the server over SSH, never waiting for a password or a prompt."""

    def __init__(self, host: str, *, ssh_port: int | None = None) -> None:
        self.host = host
        self.ssh_port = ssh_port
        self.is_root = False

    def ssh(self, remote: list[str]) -> list[str]:
        base = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
        if self.ssh_port:
            base += ["-p", str(self.ssh_port)]
        return [*base, self.host, "--", shlex.join(remote)]

    def as_root(self, args: list[str]) -> list[str]:
        return args if self.is_root else ["sudo", "-n", *args]

    def as_postgres(self, args: list[str]) -> list[str]:
        if self.is_root:
            return ["runuser", "-u", "postgres", "--", *args]
        return ["sudo", "-n", "-u", "postgres", *args]

    def run(self, remote: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(self.ssh(remote), capture_output=True, text=True, timeout=120, check=False)

    def stream(self, remote: list[str], sink: IO[bytes], progress: Callable[[int], None]) -> None:
        """Copy the remote command's stdout into ``sink``, reporting bytes received."""
        proc = subprocess.Popen(self.ssh(remote), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None and proc.stderr is not None
        received = 0
        while chunk := proc.stdout.read(CHUNK):
            sink.write(chunk)
            received += len(chunk)
            progress(received)
        error = proc.stderr.read().decode(errors="replace").strip()
        if proc.wait() != 0:
            raise PullError(f"remote command failed: {error or 'no output'}")

    # ------------------------------------------------------------------ checks

    def connect(self) -> None:
        result = self.run(["id", "-u"])
        if result.returncode != 0:
            raise PullError(
                f"cannot connect to {self.host} over SSH ({result.stderr.strip() or 'no details'}). "
                f"Check that `ssh {self.host}` works without a password (SSH keys)."
            )
        self.is_root = result.stdout.strip() == "0"
        if not self.is_root and self.run(["sudo", "-n", "true"]).returncode != 0:
            raise PullError(
                f"the SSH user on {self.host} needs password-less sudo, or connect as root "
                f"(e.g. root@{self.host.split('@')[-1]})"
            )

    def instance_info(self, name: str) -> dict[str, Any]:
        result = self.run(["cat", REMOTE_REGISTRY])
        if result.returncode != 0:
            raise PullError(
                f"{self.host} has no odoobuddy instances ({REMOTE_REGISTRY} not found). "
                "For other Odoo servers, pass --db and --remote-filestore."
            )
        instances = json.loads(result.stdout or "{}").get("instances", {})
        if name not in instances:
            known = ", ".join(sorted(instances)) or "none"
            raise PullError(f"no instance {name!r} on {self.host} (instances there: {known})")
        info: dict[str, Any] = instances[name]
        return info

    def databases(self, db_user: str) -> list[str]:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", db_user):
            raise PullError(f"unexpected database user {db_user!r}")
        sql = (
            "SELECT d.datname FROM pg_database d JOIN pg_roles r ON d.datdba = r.oid "
            f"WHERE r.rolname = '{db_user}' AND NOT d.datistemplate ORDER BY 1"
        )
        result = self.run(self.as_postgres(["psql", "-XtA", "-c", sql]))
        if result.returncode != 0:
            raise PullError(f"cannot list databases on {self.host}: {result.stderr.strip()}")
        check = f"{db_user}_install_check"
        return [line for line in result.stdout.split() if line and line != check]

    def has_dir(self, path: str) -> bool:
        return self.run(self.as_root(["test", "-d", path])).returncode == 0


@dataclass
class PullPlan:
    remote_db: str
    local_db: str
    remote_filestore: str | None  # the folder that contains <remote_db>/
    remote_version: str | None


@dataclass
class PullResult:
    local_db: str
    dump_size: int
    filestore_size: int
    neutralized: bool


def plan_pull(
    remote: Remote,
    source: Source,
    instance: Instance,
    *,
    db: str | None,
    local_db: str | None,
    remote_filestore: str | None,
    choose_db: Callable[[list[str]], str],
) -> PullPlan:
    """Work out what to copy, checking versions before anything is transferred."""
    version: str | None = None
    if source.instance:
        info = remote.instance_info(source.instance)
        version = str(info.get("version", ""))
        if remote_filestore is None and info.get("data_dir"):
            remote_filestore = f"{info['data_dir']}/filestore"
        if db is None:
            databases = remote.databases(str(info.get("db_user", "")))
            if not databases:
                raise PullError(f"instance {source.instance} on {remote.host} has no database yet")
            db = databases[0] if len(databases) == 1 else choose_db(databases)
    if db is None:
        raise PullError("give the database to copy with --db (or use USER@HOST:INSTANCE)")
    if version and major(version) != major(instance.version):
        raise PullError(
            f"the server runs Odoo {version} but {instance.name} is Odoo {instance.version}; "
            "a database can only be copied into the same Odoo version"
        )
    return PullPlan(
        remote_db=check_db_name(db),
        local_db=check_db_name(local_db or default_local_db(db)),
        remote_filestore=remote_filestore,
        remote_version=version,
    )


def execute_pull(
    runner: Runner,
    remote: Remote,
    instance: Instance,
    plan: PullPlan,
    *,
    neutralize: bool,
    copy_filestore: bool,
    step: Callable[[str, Callable[[], Any]], Any],
) -> PullResult:
    """Copy, restore and neutralize. ``step(title, action)`` shows progress in the UI."""
    settings = postgres.read_db_settings(instance.config_path)
    env = postgres.client_env(settings)
    result = PullResult(local_db=plan.local_db, dump_size=0, filestore_size=0, neutralized=False)

    def progress(label: str) -> Callable[[int], None]:
        def report(received: int) -> None:
            if runner.on_output:
                runner.on_output(f"{label}: {human_size(received)} received")

        return report

    with tempfile.TemporaryDirectory(prefix="odoobuddy-pull-") as tmp:
        dump = Path(tmp) / "dump.pgdump"

        def copy_database() -> None:
            command = remote.as_postgres(
                ["pg_dump", "--no-owner", "--no-privileges", "--format=custom", plan.remote_db]
            )
            with dump.open("wb") as sink:
                remote.stream(command, sink, progress("database"))
            result.dump_size = dump.stat().st_size

        step(f"Copy database {plan.remote_db} from {remote.host}", copy_database)

        files = Path(tmp) / "filestore"
        filestore_dir = plan.remote_filestore
        if copy_filestore and filestore_dir and remote.has_dir(f"{filestore_dir}/{plan.remote_db}"):

            def copy_files() -> None:
                files.mkdir()
                archive = Path(tmp) / "filestore.tar"
                with archive.open("wb") as sink:
                    remote.stream(
                        remote.as_root(["tar", "-C", filestore_dir, "-cf", "-", plan.remote_db]),
                        sink,
                        progress("filestore"),
                    )
                result.filestore_size = archive.stat().st_size
                with tarfile.open(archive) as tar:
                    _safe_extract(tar, files)

            step("Copy filestore (attachments)", copy_files)

        def restore() -> None:
            runner.run(["dropdb", "--if-exists", "--force", plan.local_db], env=env)
            runner.run(["createdb", "--owner", settings["user"], plan.local_db], env=env)
            restored = runner.run(
                [
                    "pg_restore",
                    "--no-owner",
                    "--no-privileges",
                    "--no-comments",
                    "-d",
                    plan.local_db,
                    str(dump),
                ],
                env=env,
                check=False,
            )
            # pg_restore also "fails" on harmless things (e.g. extensions it may not own):
            # judge the result by whether Odoo's tables are there.
            probe = runner.run(
                ["psql", "-XtA", "-d", plan.local_db, "-c", "SELECT count(*) FROM ir_module_module"],
                env=env,
                check=False,
            )
            if probe.returncode != 0 and not runner.dry_run:
                raise CommandError(restored.args, restored.returncode, restored.stdout[-2000:])
            target = instance.data_dir / "filestore" / plan.local_db
            source_files = files / plan.remote_db
            if source_files.is_dir() and not runner.dry_run:
                shutil.rmtree(target, ignore_errors=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source_files), str(target))

        step(f"Restore into local database {plan.local_db}", restore)

    if neutralize:

        def run_neutralize() -> None:
            command = [str(instance.python_bin), str(instance.odoo_bin), "neutralize"]
            command += ["-c", str(instance.config_path), "-d", plan.local_db]
            try:
                runner.run(command, cwd=instance.instance_dir)
            except CommandError as exc:
                last = exc.output.strip().splitlines()[-1:] or [str(exc)]
                raise PullError(
                    f"neutralizing failed, so database {plan.local_db} is NOT safe: it could send "
                    "real emails or run payments. Do not start Odoo with it; drop it with: "
                    f"dropdb {plan.local_db}   (details: {last[0]})"
                ) from None

        step("Neutralize (disable emails, scheduled actions, payments...)", run_neutralize)
        result.neutralized = True
    return result
