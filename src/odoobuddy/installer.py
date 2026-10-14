"""Turning an InstallSpec into a working Odoo instance, step by step."""

from __future__ import annotations

import secrets
import shlex
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import postgres, render, sources, system
from .layout import Layout
from .models import IDE_NAMES, InstallSpec, Instance, db_role_for
from .postgres import client_env, read_db_settings
from .registry import MARKER_FILE, Registry
from .system import CommandError, Runner, command_exists, os_release, ssh_port, wait_for_port
from .versions import no_demo_flags, python_for

APT_BASE = [
    "git",
    "curl",
    "ca-certificates",
    "build-essential",
    "libxml2-dev",
    "libxslt1-dev",
    "libldap2-dev",
    "libsasl2-dev",
    "libpq-dev",
    "libjpeg-dev",
    "zlib1g-dev",
    "libffi-dev",
    "libssl-dev",
    "postgresql",
    "postgresql-client",
    "fontconfig",
    "xfonts-75dpi",
    "xfonts-base",
]
APT_SERVER = ["nginx", "certbot", "python3-certbot-nginx", "ufw"]

WKHTMLTOPDF_RELEASE = "0.12.6.1-3"
# Odoo needs the "patched Qt" build of wkhtmltopdf. Noble reuses the jammy package.
WKHTMLTOPDF_CODENAMES = {"jammy": "jammy", "noble": "jammy", "bookworm": "bookworm"}

# Installed into a throw-away database to prove the instance works. It pulls in mail,
# web and their assets, so most dependency or Python package problems show up here.
CHECK_MODULE = "contacts"

# markdown2: Odoo 20 renders markdown with it but does not list it in requirements.txt.
EXTRA_PACKAGES = ["setuptools<81", "wheel", "markdown2"]
LOCAL_DEV_PACKAGES = ["debugpy", "watchdog"]


def find_uv() -> str:
    try:
        from uv import find_uv_bin

        return str(find_uv_bin())
    except (ImportError, FileNotFoundError):
        return "uv"


def generate_password(length: int = 24) -> str:
    return secrets.token_urlsafe(length)[:length]


def wkhtmltopdf_url(codename: str, arch: str) -> str | None:
    target = WKHTMLTOPDF_CODENAMES.get(codename)
    if target is None:
        return None
    return (
        "https://github.com/wkhtmltopdf/packaging/releases/download/"
        f"{WKHTMLTOPDF_RELEASE}/wkhtmltox_{WKHTMLTOPDF_RELEASE}.{target}_{arch}.deb"
    )


def suggested_workers(cpus: int, ram_mb: int) -> int:
    """Odoo's rule of thumb (2 x CPU + 1), capped so workers fit in RAM (~300 MB each)."""
    by_cpu = cpus * 2 + 1
    by_ram = max(1, (ram_mb - 1024) // 300) if ram_mb else by_cpu
    return max(2, min(by_cpu, by_ram))


@dataclass
class Step:
    title: str
    action: Callable[[], None]


@dataclass
class Credentials:
    db_password: str
    master_password: str


class Installer:
    def __init__(
        self,
        spec: InstallSpec,
        *,
        layout: Layout,
        registry: Registry,
        runner: Runner,
    ) -> None:
        self.spec = spec
        self.layout = layout
        self.registry = registry
        self.runner = runner
        self.credentials = Credentials(
            db_password=generate_password(),
            master_password=generate_password(),
        )
        self.instance = self._build_instance()
        self.warnings: list[str] = []

    def _build_instance(self) -> Instance:
        spec, layout = self.spec, self.layout
        instance_dir = layout.instance_dir(spec.mode, spec.name)
        return Instance(
            name=spec.name,
            version=spec.version,
            edition=spec.edition,
            mode=spec.mode,
            http_port=spec.http_port,
            gevent_port=spec.gevent_port,
            db_user=db_role_for(spec.name),
            python=python_for(spec.version),
            instance_dir=instance_dir,
            config_path=layout.config_path(spec.mode, spec.name),
            log_path=layout.log_path(spec.mode, spec.name),
            data_dir=layout.data_dir(spec.mode, spec.name),
            venv_dir=instance_dir / "venv",
            community_dir=layout.community_dir(spec.mode, spec.name),
            enterprise_dir=layout.enterprise_dir(spec.mode, spec.name)
            if spec.edition == "enterprise"
            else None,
            domain=spec.domain if spec.mode == "server" else None,
            ssl=spec.ssl and spec.mode == "server" and bool(spec.domain),
            server_ip=system.server_address() if spec.mode == "server" and not spec.domain else None,
            ide=spec.ide if spec.mode == "local" else "none",
            run_mode=spec.run_mode if spec.mode == "local" else "background",
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    # ------------------------------------------------------------------ plan

    def plan(self) -> list[Step]:
        inst = self.instance
        server = inst.mode == "server"
        steps = [
            Step("Install system packages", self.install_system_packages),
            Step("Install wkhtmltopdf (PDF reports)", self.install_wkhtmltopdf),
            Step("Start PostgreSQL", self.start_postgres),
            Step(f"Download Odoo {inst.version} Community", self.download_community),
        ]
        if inst.enterprise_dir is not None:
            steps.append(Step(f"Download Odoo {inst.version} Enterprise", self.download_enterprise))
        if self.spec.repos:
            count = len(self.spec.repos)
            steps.append(
                Step(f"Clone {count} custom repositor{'y' if count == 1 else 'ies'}", self.clone_repos)
            )
        steps.append(Step(f"Create Python {inst.python} environment", self.create_python_env))
        steps.append(Step(f"Create database user {inst.db_user}", self.create_db_role))
        if server:
            steps.append(Step(f"Create system user {inst.system_user}", self.create_system_user))
        steps += [
            Step("Write configuration", self.write_config),
            Step(f"Test installation ({CHECK_MODULE} in a temporary database)", self.test_installation),
        ]
        if not server and inst.ide != "none":
            ide_name = IDE_NAMES[inst.ide]
            if self.spec.install_editor:
                steps.append(Step(f"Install {ide_name}", self.install_editor))
            steps.append(Step(f"Configure {ide_name}", self.configure_ide))
        if server:
            steps.append(Step(f"Create service {inst.service}", self.install_service))
            where = inst.domain or f"port {inst.http_port}"
            steps.append(Step(f"Configure Nginx for {where}", self.configure_nginx))
            if inst.ssl:
                steps.append(Step("Get SSL certificate (Let's Encrypt)", self.obtain_ssl))
            if self.spec.firewall:
                steps.append(Step("Configure firewall", self.configure_firewall))
            steps.append(Step("Configure log rotation", self.configure_logrotate))
        steps.append(Step("Register instance", self.register))
        return steps

    # ------------------------------------------------------------ the steps

    def install_system_packages(self) -> None:
        packages = APT_BASE + (APT_SERVER if self.instance.mode == "server" else [])
        apt = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get"]
        self.runner.run([*apt, "update"], sudo=True)
        self.runner.run([*apt, "install", "-y", "--no-install-recommends", *packages], sudo=True)

    def install_wkhtmltopdf(self) -> None:
        if command_exists("wkhtmltopdf") and not self.runner.dry_run:
            return
        codename = os_release().get("VERSION_CODENAME", "")
        arch = self.runner.run(["dpkg", "--print-architecture"]).stdout.strip() or "amd64"
        url = wkhtmltopdf_url(codename, arch)
        apt = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y"]
        if url is None:
            # Unknown distribution: fall back to the distro package (works, but no headers/footers).
            self.runner.run([*apt, "wkhtmltopdf"], sudo=True)
            return
        deb = Path("/tmp") / Path(url).name
        self.runner.run(["curl", "-fsSL", "-o", str(deb), url])
        self.runner.run([*apt, str(deb)], sudo=True)

    def start_postgres(self) -> None:
        self.runner.run(["systemctl", "enable", "--now", "postgresql"], sudo=True)

    def download_community(self) -> None:
        sources.clone_community(self.runner, self.instance.version, self.instance.community_dir)

    def download_enterprise(self) -> None:
        assert self.instance.enterprise_dir is not None
        token = self.spec.github_token or ""
        sources.clone_enterprise(self.runner, self.instance.version, self.instance.enterprise_dir, token)

    def create_python_env(self) -> None:
        inst = self.instance
        uv = find_uv()
        env: dict[str, str] = {}
        python_dir = self.layout.python_install_dir(inst.mode)
        if python_dir is not None:
            env["UV_PYTHON_INSTALL_DIR"] = str(python_dir)
        self.runner.mkdir(inst.instance_dir)
        if not inst.python_bin.exists() or self.runner.dry_run:
            # Only uv-managed Pythons: they ship Python.h, which python-ldap and others need
            # to compile. A system Python of the same version (Ubuntu 24.04 has 3.12) has no
            # headers unless python3.X-dev is installed.
            self.runner.run(
                [
                    uv,
                    "venv",
                    "--seed",
                    "--python",
                    inst.python,
                    "--python-preference",
                    "only-managed",
                    str(inst.venv_dir),
                ],
                env=env,
            )
        packages = EXTRA_PACKAGES + (LOCAL_DEV_PACKAGES if inst.mode == "local" else [])
        requirements = [str(inst.community_dir / "requirements.txt")]
        if inst.custom_dir.is_dir():
            requirements += [
                str(p / "requirements.txt")
                for p in sorted(inst.custom_dir.iterdir())
                if (p / "requirements.txt").is_file()
            ]
        self.runner.run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(inst.python_bin),
                *[arg for req in requirements for arg in ("-r", req)],
                *packages,
            ],
            env=env,
        )
        from . import editors

        editors.install_debug_hook(self.runner, inst)  # also needed on servers: see DEBUG_HOOK

    def create_db_role(self) -> None:
        postgres.ensure_role(self.runner, self.instance.db_user, self.credentials.db_password)

    def create_system_user(self) -> None:
        inst = self.instance
        exists = self.runner.run(["id", "-u", inst.system_user], check=False)
        if exists.returncode == 0 and not self.runner.dry_run:
            return
        self.runner.run(
            [
                "useradd",
                "--system",
                "--user-group",
                "--no-create-home",
                "--home-dir",
                str(inst.instance_dir),
                "--shell",
                "/usr/sbin/nologin",
                inst.system_user,
            ]
        )

    def write_config(self) -> None:
        inst = self.instance
        server = inst.mode == "server"
        owner = inst.system_user if server else None
        self.runner.mkdir(inst.instance_dir)
        self.runner.mkdir(inst.custom_dir / "addons")
        self.runner.mkdir(inst.config_path.parent)
        self.runner.mkdir(inst.data_dir, owner=owner, mode=0o750 if server else 0o755)
        self.runner.mkdir(inst.log_path.parent)
        if server:
            self.runner.write_file(inst.log_path, "", mode=0o640, owner=owner)
        self.runner.write_file(
            inst.config_path,
            render.odoo_conf(
                inst,
                db_password=self.credentials.db_password,
                master_password=self.credentials.master_password,
                workers=self.spec.workers,
            ),
            mode=0o640 if server else 0o600,
            owner=owner,
        )
        self.runner.write_file(inst.instance_dir / MARKER_FILE, inst.name + "\n")
        if not server:
            self.runner.write_file(inst.instance_dir / "start.sh", render.start_script(inst), mode=0o755)

    def _odoo_cmd(self, *args: str, subcommand: str | None = None) -> list[str]:
        inst = self.instance
        sub = [subcommand] if subcommand else []
        return [str(inst.python_bin), str(inst.odoo_bin), *sub, "-c", str(inst.config_path), *args]

    def _as_user(self) -> str | None:
        return self.instance.system_user if self.instance.mode == "server" else None

    def clone_repos(self) -> None:
        from .repos import clone_repo

        for repo in self.spec.repos:
            try:
                clone_repo(self.runner, self.instance, repo.url, repo.branch, token=repo.token)
            except FileExistsError:
                continue  # already cloned by an earlier, interrupted run

    @property
    def check_db(self) -> str:
        return f"{self.instance.db_user}_install_check"

    def test_installation(self) -> None:
        """Prove the setup works by installing a real module, then throw the database away.

        The user creates their own first database afterwards (name, language, country,
        demo data and admin password are all chosen in Odoo's database manager).
        """
        inst = self.instance
        self.drop_check_db()  # leftover from an interrupted run
        cmd = self._odoo_cmd("-d", self.check_db, "-i", f"base,{CHECK_MODULE}", "--stop-after-init")
        cmd += no_demo_flags(inst.version)
        if inst.mode == "server":
            cmd.append("--logfile=/dev/stderr")  # show progress/errors instead of the log file
        try:
            self.runner.run(cmd, as_user=self._as_user(), cwd=inst.instance_dir)
        finally:
            self.drop_check_db()

    def drop_check_db(self) -> None:
        inst = self.instance
        if self.runner.dry_run:
            self.runner.run(["dropdb", "--if-exists", self.check_db])
            return
        env = client_env(read_db_settings(inst.config_path))
        self.runner.run(["dropdb", "--if-exists", "--force", self.check_db], env=env)
        shutil.rmtree(inst.data_dir / "filestore" / self.check_db, ignore_errors=True)

    def install_editor(self) -> None:
        """Install the editor the user asked for. A failure doesn't stop the Odoo install."""
        from .ide import editor_install, install_hint

        inst = self.instance
        how = editor_install(inst.ide)
        name = IDE_NAMES[inst.ide]
        if how is None:
            self.warnings.append(f"{name} could not be installed automatically: {install_hint(inst.ide)}")
            return
        try:
            self.runner.run(how.command, sudo=how.sudo)
        except CommandError as exc:
            last = exc.output.strip().splitlines()[-1:] or [str(exc)]
            self.warnings.append(
                f"Installing {name} failed ({last[0]}). Install it yourself: {install_hint(inst.ide)}, "
                f"then run: odoobuddy open {inst.name}"
            )

    def configure_ide(self) -> None:
        from . import editors

        editors.configure(self.runner, self.instance)

    def install_service(self) -> None:
        inst = self.instance
        unit = self.layout.systemd_dir / f"{inst.service}.service"
        self.runner.write_file(unit, render.systemd_unit(inst))
        self.runner.run(["systemctl", "daemon-reload"])
        self.runner.run(["systemctl", "enable", "--now", inst.service])
        if not self.runner.dry_run and not wait_for_port(inst.backend_http_port, 60):
            self.warnings.append(
                f"Odoo did not answer within 60s. Check: systemctl status {inst.service} and {inst.log_path}"
            )

    def configure_nginx(self) -> None:
        inst = self.instance
        available = self.layout.nginx_dir / "sites-available" / f"odoo-{inst.name}.conf"
        enabled = self.layout.nginx_dir / "sites-enabled" / f"odoo-{inst.name}.conf"
        self.runner.write_file(available, render.nginx_site(inst))
        self.runner.mkdir(enabled.parent)
        self.runner.run(["ln", "-sfn", str(available), str(enabled)])
        self.runner.run(["nginx", "-t"])
        self.runner.run(["systemctl", "reload", "nginx"])

    def obtain_ssl(self) -> None:
        inst = self.instance
        assert inst.domain
        cmd = ["certbot", "--nginx", "-d", inst.domain, "--non-interactive", "--agree-tos"]
        cmd += ["--redirect"]
        cmd += ["-m", self.spec.email] if self.spec.email else ["--register-unsafely-without-email"]
        try:
            self.runner.run(cmd)
        except CommandError as exc:
            # Usually DNS is not pointing here yet. Odoo works over HTTP meanwhile, so don't
            # throw away a finished install: explain how to retry instead.
            inst.ssl = False
            self.warnings.append(
                f"SSL certificate failed ({exc.output.splitlines()[-1] if exc.output else exc}). "
                f"Once {inst.domain} points to this server, run: sudo {shlex.join(cmd)}"
            )

    def configure_firewall(self) -> None:
        inst = self.instance
        self.runner.run(["ufw", "allow", f"{ssh_port() or 22}/tcp"])
        if inst.domain:
            self.runner.run(["ufw", "allow", "Nginx Full"])
        else:
            self.runner.run(["ufw", "allow", f"{inst.http_port}/tcp"])
        self.runner.run(["ufw", "--force", "enable"])

    def configure_logrotate(self) -> None:
        inst = self.instance
        self.runner.write_file(self.layout.logrotate_dir / f"odoo-{inst.name}", render.logrotate(inst))

    def register(self) -> None:
        if self.runner.dry_run:
            self.runner.console.print(f"  [dim]register {self.instance.name}[/dim]")
            return
        self.registry.add(self.instance)
        self.registry.set_current(self.instance.name)
