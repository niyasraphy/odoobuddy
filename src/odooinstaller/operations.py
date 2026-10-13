"""Managing existing instances: start/stop, logs, update, backup/restore, remove, doctor."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import postgres, sources
from .installer import find_uv
from .layout import Layout
from .models import Instance
from .registry import Registry
from .system import CommandError, Runner, command_exists, is_root, port_in_use

STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"


def _odoo(instance: Instance, *args: str, subcommand: str | None = None) -> list[str]:
    sub = [subcommand] if subcommand else []
    return [
        str(instance.python_bin),
        str(instance.odoo_bin),
        *sub,
        "-c",
        str(instance.config_path),
        *args,
    ]


def _as_user(instance: Instance) -> str | None:
    return instance.system_user if instance.mode == "server" else None


# ------------------------------------------------------------------ status


def _local_pid(instance: Instance) -> int | None:
    try:
        pid = int(instance.pid_file.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid


def status(instance: Instance) -> str:
    if instance.mode == "server":
        result = subprocess.run(["systemctl", "is-active", "--quiet", instance.service], check=False)
        return STATUS_RUNNING if result.returncode == 0 else STATUS_STOPPED
    return STATUS_RUNNING if _local_pid(instance) else STATUS_STOPPED


def _wait_for_port(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


# ------------------------------------------------------------- lifecycle


def start(runner: Runner, instance: Instance, *, wait: float = 30) -> bool:
    """Start the instance. Returns True once Odoo answers on its port."""
    if instance.mode == "server":
        runner.run(["systemctl", "start", instance.service], sudo=True)
    else:
        if _local_pid(instance):
            return True
        if runner.dry_run:
            runner.console.print(f"  [dim]start {instance.name} in background[/dim]")
            return True
        instance.log_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            _odoo(instance, f"--logfile={instance.log_path}", f"--pidfile={instance.pid_file}"),
            cwd=instance.instance_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    if runner.dry_run:
        return True
    return _wait_for_port(instance.http_port, wait)


def stop(runner: Runner, instance: Instance) -> None:
    if instance.mode == "server":
        runner.run(["systemctl", "stop", instance.service], sudo=True)
        return
    pid = _local_pid(instance)
    if pid is None or runner.dry_run:
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        time.sleep(0.5)
        if _local_pid(instance) is None:
            break
    else:
        os.kill(pid, signal.SIGKILL)
    instance.pid_file.unlink(missing_ok=True)


def restart(runner: Runner, instance: Instance) -> bool:
    if instance.mode == "server":
        runner.run(["systemctl", "restart", instance.service], sudo=True)
        return runner.dry_run or _wait_for_port(instance.http_port, 30)
    stop(runner, instance)
    return start(runner, instance)


def logs_command(instance: Instance, lines: int, follow: bool) -> list[str]:
    cmd = ["tail", "-n", str(lines)] + (["-F"] if follow else []) + [str(instance.log_path)]
    if instance.mode == "server" and not is_root():
        cmd = ["sudo", *cmd]
    return cmd


def list_databases(runner: Runner, instance: Instance) -> list[str]:
    """Databases owned by the instance's PostgreSQL user, i.e. the ones its users created."""
    settings = postgres.read_db_settings(instance.config_path)
    result = runner.run(
        [
            "psql",
            "-X",
            "-tA",
            "-d",
            "postgres",
            "-c",
            "SELECT d.datname FROM pg_database d JOIN pg_roles r ON d.datdba = r.oid "
            "WHERE r.rolname = current_user AND NOT d.datistemplate ORDER BY 1",
        ],
        env=postgres.client_env(settings),
    )
    check_db = f"{instance.db_user}_install_check"
    return [line.strip() for line in result.stdout.splitlines() if line.strip() and line.strip() != check_db]


def shell_command(instance: Instance, db: str) -> list[str]:
    cmd = _odoo(instance, "-d", db, subcommand="shell")
    if instance.mode == "server":
        prefix = (
            ["runuser", "-u", instance.system_user, "--"]
            if is_root()
            else ["sudo", "-u", instance.system_user, "-H", "--"]
        )
        cmd = [*prefix, *cmd]
    return cmd


# ---------------------------------------------------------------- update


def update(
    runner: Runner,
    instance: Instance,
    *,
    github_token: str | None,
    modules: str = "all",
    backup_dir: Path | None = None,
) -> list[str]:
    """Pull Odoo and custom repositories, then upgrade modules in every database.

    Returns warnings (e.g. a custom repository that could not be pulled).
    """
    from . import repos

    warnings: list[str] = []
    databases = list_databases(runner, instance)
    if instance.mode == "server":
        for db in databases:
            backup(runner, instance, db, backup_dir or default_backup_dir(instance))
    sources.update(runner, instance.community_dir, instance.version)
    if instance.enterprise_dir is not None and (instance.enterprise_dir / ".git").exists():
        if github_token:
            sources.update(
                runner, instance.enterprise_dir, instance.version, env=sources.git_auth_env(github_token)
            )
        else:
            warnings.append("Enterprise code not updated (no GitHub token given)")
    for repo in repos.list_repos(runner, instance):
        try:
            repos.pull(runner, repo.path)
        except CommandError as exc:
            warnings.append(f"custom repository {repo.name} not updated: {exc.output.splitlines()[-1:]}")
    requirements = [instance.community_dir / "requirements.txt"]
    requirements += [
        r.path / "requirements.txt"
        for r in repos.list_repos(runner, instance)
        if (r.path / "requirements.txt").is_file()
    ]
    runner.run(
        [
            find_uv(),
            "pip",
            "install",
            "--python",
            str(instance.python_bin),
            *[arg for req in requirements for arg in ("-r", str(req))],
        ]
    )
    repos.refresh_addons_path(runner, instance)
    was_running = status(instance) == STATUS_RUNNING
    if was_running:
        stop(runner, instance)
    for db in databases:
        runner.run(
            _odoo(instance, "-d", db, "-u", modules, "--stop-after-init"),
            as_user=_as_user(instance),
            cwd=instance.instance_dir,
        )
    if was_running:
        start(runner, instance)
    return warnings


# ------------------------------------------------------- backup / restore


def default_backup_dir(instance: Instance) -> Path:
    if instance.mode == "server":
        return Path("/var/backups/odooinstaller") / instance.name
    return instance.instance_dir / "backups"


def backup(runner: Runner, instance: Instance, db: str, dest_dir: Path) -> Path:
    """Write a zip in Odoo's own backup format: dump.sql + filestore/ + manifest.json."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = dest_dir / f"{instance.name}_{db}_{stamp}.zip"
    if runner.dry_run:
        runner.console.print(f"  [dim]backup {db} -> {target}[/dim]")
        return target
    dest_dir.mkdir(parents=True, exist_ok=True)
    settings = postgres.read_db_settings(instance.config_path)
    with tempfile.TemporaryDirectory(prefix="odooinstaller-") as tmp:
        dump = Path(tmp) / "dump.sql"
        runner.run(
            ["pg_dump", "--no-owner", "--format=plain", "--file", str(dump), db],
            env=postgres.client_env(settings),
        )
        manifest = {
            "odoo_dump": "1",
            "db_name": db,
            "version": instance.version,
            "major_version": instance.version,
            "created_by": "odooinstaller",
        }
        filestore = instance.data_dir / "filestore" / db
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(dump, "dump.sql")
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            if filestore.is_dir():
                for path in sorted(filestore.rglob("*")):
                    if path.is_file():
                        zf.write(path, Path("filestore") / path.relative_to(filestore))
    target.chmod(0o600)
    return target


def restore(runner: Runner, instance: Instance, archive: Path, db: str) -> None:
    """Restore ``archive`` into database ``db``, replacing it (and its files) if it exists."""
    if not zipfile.is_zipfile(archive):
        raise ValueError(f"{archive} is not a zip backup")
    with zipfile.ZipFile(archive) as zf:
        if "dump.sql" not in zf.namelist():
            raise ValueError(f"{archive} has no dump.sql (not an Odoo backup?)")
    if runner.dry_run:
        runner.console.print(f"  [dim]restore {archive} -> {db}[/dim]")
        return
    was_running = status(instance) == STATUS_RUNNING
    if was_running:
        stop(runner, instance)
    settings = postgres.read_db_settings(instance.config_path)
    env = postgres.client_env(settings)
    filestore = instance.data_dir / "filestore" / db
    with tempfile.TemporaryDirectory(prefix="odooinstaller-") as tmp:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
        runner.run(["dropdb", "--if-exists", "--force", db], env=env)
        runner.run(["createdb", "--owner", settings["user"], db], env=env)
        runner.run(
            ["psql", "-v", "ON_ERROR_STOP=1", "-q", "-f", str(Path(tmp) / "dump.sql"), db],
            env=env,
        )
        shutil.rmtree(filestore, ignore_errors=True)
        extracted = Path(tmp) / "filestore"
        if extracted.is_dir():
            shutil.copytree(extracted, filestore)
    if instance.mode == "server":
        runner.run(["chown", "-R", f"{instance.system_user}:{instance.system_user}", str(instance.data_dir)])
    if was_running:
        start(runner, instance)


# ----------------------------------------------------------------- remove


def remove(runner: Runner, registry: Registry, layout: Layout, instance: Instance) -> None:
    """Delete everything that belongs to the instance. Shared source code is kept."""
    stop(runner, instance)
    if instance.mode == "server":
        unit = layout.systemd_dir / f"{instance.service}.service"
        runner.run(["systemctl", "disable", instance.service], check=False)
        _delete(runner, unit)
        runner.run(["systemctl", "daemon-reload"], check=False)
        if instance.domain:
            for folder in ("sites-enabled", "sites-available"):
                _delete(runner, layout.nginx_dir / folder / f"odoo-{instance.name}.conf")
            runner.run(["systemctl", "reload", "nginx"], check=False)
            if instance.ssl:
                runner.run(
                    ["certbot", "delete", "--non-interactive", "--cert-name", instance.domain],
                    check=False,
                )
        else:
            runner.run(["ufw", "delete", "allow", f"{instance.http_port}/tcp"], check=False)
        _delete(runner, layout.logrotate_dir / f"odoo-{instance.name}")
    try:
        postgres.drop_role_and_databases(runner, instance.db_user)
    except CommandError as exc:
        runner.console.print(f"  [yellow]could not drop database user: {exc}[/yellow]")
    for path in (instance.data_dir, instance.config_path, instance.log_path, instance.instance_dir):
        _delete(runner, path)
    if instance.mode == "server":
        runner.run(["userdel", instance.system_user], check=False)
    if not runner.dry_run:
        registry.remove(instance.name)


def _delete(runner: Runner, path: Path) -> None:
    if runner.dry_run:
        runner.console.print(f"  [dim]delete {path}[/dim]")
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


# ----------------------------------------------------------------- doctor


@dataclass
class Check:
    ok: bool
    subject: str
    message: str
    fix: str = ""


def doctor(registry: Registry) -> list[Check]:
    checks: list[Check] = []
    for tool, fix in (
        ("git", "sudo apt install git"),
        ("psql", "sudo apt install postgresql"),
        ("wkhtmltopdf", "re-run odooinstaller install, or see the README"),
    ):
        found = command_exists(tool)
        checks.append(Check(found, "system", f"{tool} {'found' if found else 'not found'}", fix))

    pg = subprocess.run(["pg_isready", "-q"], check=False) if command_exists("pg_isready") else None
    pg_ok = pg is not None and pg.returncode == 0
    checks.append(
        Check(
            pg_ok,
            "system",
            "PostgreSQL is accepting connections" if pg_ok else "PostgreSQL is not running",
            "sudo systemctl start postgresql",
        )
    )

    free_gb = shutil.disk_usage(Path.home()).free / 1024**3
    checks.append(Check(free_gb >= 2, "system", f"{free_gb:.1f} GB free disk space", "free up disk space"))

    instances = registry.all()
    seen: dict[int, str] = {}
    for inst in sorted(instances.values(), key=lambda i: i.name):
        for path, what in (
            (inst.config_path, "config file"),
            (inst.python_bin, "Python environment"),
            (inst.odoo_bin, "Odoo source code"),
        ):
            exists = path.exists()
            checks.append(
                Check(
                    exists,
                    inst.name,
                    f"{what} {'ok' if exists else 'missing'} ({path})",
                    f"re-run: odooinstaller install --name {inst.name}",
                )
            )
        for port in (inst.http_port, inst.gevent_port):
            if port in seen:
                checks.append(
                    Check(
                        False,
                        inst.name,
                        f"port {port} also used by {seen[port]}",
                        "change http_port/gevent_port in the config file",
                    )
                )
            seen[port] = inst.name
        running = status(inst) == STATUS_RUNNING
        if not running and port_in_use(inst.http_port):
            checks.append(
                Check(
                    False,
                    inst.name,
                    f"stopped, but port {inst.http_port} is taken by another program",
                    f"find it with: sudo ss -ltnp 'sport = :{inst.http_port}'",
                )
            )
        checks.append(Check(True, inst.name, "running" if running else "stopped"))
    return checks
