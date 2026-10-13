"""The `odooinstaller` command."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel

from . import __version__, operations, sources, ui
from .installer import Installer, suggested_workers
from .layout import Layout
from .models import Instance
from .registry import Registry, RegistryError, resolve
from .system import CommandError, Runner, cpu_count, is_root, memory_mb, os_release, port_in_use
from .versions import available_versions
from .wizard import QuestionaryPrompter, WizardAbort, WizardContext, run_wizard

app = typer.Typer(
    help="Install and manage multiple Odoo instances, on your computer or on a server.",
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode="rich",
)
console = ui.console

SUPPORTED_OS = {("ubuntu", "jammy"), ("ubuntu", "noble"), ("debian", "bookworm")}


def _layout() -> Layout:
    return Layout.default()


def _registry() -> Registry:
    return Registry(_layout())


def _fail(message: str, code: int = 1) -> typer.Exit:
    console.print(f"[red]✗ {message}[/red]")
    return typer.Exit(code)


def _choose(instances: list[Instance]) -> Instance:
    if not sys.stdin.isatty():
        raise RegistryError("which instance? pass its name, e.g. odooinstaller start shop")
    statuses = {i.name: operations.status(i) for i in instances}
    choices = [
        (i.name, f"{i.name:<14} {i.version:<6} {i.edition.title():<11} {i.mode:<7} {statuses[i.name]}")
        for i in instances
    ]
    prompter = QuestionaryPrompter(console.print)
    try:
        picked = prompter.select("Which instance?", choices)
    except WizardAbort:
        raise typer.Exit(1) from None
    return next(i for i in instances if i.name == picked)


def _instance(name: Optional[str]) -> Instance:
    try:
        return resolve(_registry(), name, cwd=Path.cwd(), choose=_choose)
    except RegistryError as exc:
        raise _fail(str(exc)) from None


def _require_root_for(instance: Instance, action: str) -> None:
    if instance.mode == "server" and not is_root():
        raise _fail(f"{action} a server instance needs root: sudo odooinstaller {' '.join(sys.argv[1:])}")


def _confirm_by_typing(instance: Instance, warning: str) -> None:
    console.print(f"[yellow]⚠ {warning}[/yellow]")
    typed = typer.prompt("  Type the instance name to confirm")
    if typed.strip() != instance.name:
        raise _fail("name did not match, nothing was changed")


def _runner(dry_run: bool, log_name: str) -> Runner:
    log_dir = Path("/var/log/odooinstaller") if is_root() else _layout().state_dir / "logs"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Runner(console=console, dry_run=dry_run, log_file=log_dir / f"{log_name}-{stamp}.log")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"odooinstaller {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """With no command, starts the install wizard."""
    if ctx.invoked_subcommand is None:
        # Invoke the click command (not the Python function) so option defaults are applied.
        install_command = ctx.command.get_command(ctx, "install")  # type: ignore[attr-defined]
        ctx.invoke(install_command)


# ------------------------------------------------------------------ install


@app.command()
def install(
    name: Optional[str] = typer.Option(None, help="Instance name, e.g. shop"),
    odoo_version: Optional[str] = typer.Option(None, "--version", "-v", help="Odoo version, e.g. 19.0"),
    edition: Optional[str] = typer.Option(None, help="community or enterprise"),
    mode: Optional[str] = typer.Option(None, help="local or server"),
    port: Optional[int] = typer.Option(None, help="Web port (websocket port is chosen automatically)"),
    demo: Optional[bool] = typer.Option(None, "--demo/--no-demo", help="Load demo data"),
    ide: Optional[str] = typer.Option(None, help="Local only: vscode, pycharm or none"),
    domain: Optional[str] = typer.Option(None, help="Server only: domain name, e.g. erp.example.com"),
    no_domain: bool = typer.Option(False, "--no-domain", help="Server only: no domain (no Nginx/SSL)"),
    ssl: Optional[bool] = typer.Option(None, "--ssl/--no-ssl", help="Server only: Let's Encrypt HTTPS"),
    email: Optional[str] = typer.Option(None, help="Server only: email for SSL notices"),
    workers: Optional[int] = typer.Option(None, help="Server only: number of Odoo workers"),
    firewall: Optional[bool] = typer.Option(None, "--firewall/--no-firewall", help="Server only: enable ufw"),
    own_source: bool = typer.Option(False, "--own-source", help="Private copy of the Odoo code"),
    github_token: Optional[str] = typer.Option(
        None,
        envvar="ODOO_GITHUB_TOKEN",
        show_envvar=True,
        help="Token with odoo/enterprise access (prefer the env var over typing it here)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for final confirmation"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen, change nothing"),
) -> None:
    """Install a new Odoo instance (asks questions for anything not given as an option)."""
    release = os_release()
    if (release.get("ID"), release.get("VERSION_CODENAME")) not in SUPPORTED_OS:
        console.print(
            f"[yellow]⚠ {release.get('PRETTY_NAME', 'This system')} is not tested. "
            "Supported: Ubuntu 22.04/24.04, Debian 12.[/yellow]"
        )

    preset: dict[str, object] = {}
    for key, value in {
        "name": name,
        "version": odoo_version,
        "edition": edition,
        "mode": mode,
        "http_port": port,
        "demo": demo,
        "ide": ide,
        "ssl": ssl,
        "email": email,
        "workers": workers,
        "firewall": firewall,
        "github_token": github_token,
    }.items():
        if value is not None:
            preset[key] = value
    if domain is not None:
        preset["domain"] = domain
    elif no_domain:
        preset["domain"] = None
    preset["own_source"] = own_source
    for key, allowed in (
        ("edition", {"community", "enterprise"}),
        ("mode", {"local", "server"}),
        ("ide", {"vscode", "pycharm", "none"}),
    ):
        if key in preset and preset[key] not in allowed:
            raise _fail(f"--{key} must be one of: {', '.join(sorted(allowed))}")

    registry = _registry()
    ctx = WizardContext(
        instances=registry.all(),
        versions=lambda: _with_status("Reading Odoo versions from GitHub…", available_versions),
        port_in_use=port_in_use,
        check_enterprise=lambda token: _with_status(
            "Checking GitHub access…", lambda: sources.check_enterprise_access(token)
        ),
        is_root=is_root() or dry_run,
        suggested_workers=suggested_workers(cpu_count(), memory_mb()),
        preset=preset,
    )
    try:
        spec = run_wizard(QuestionaryPrompter(console.print), ctx)
    except WizardAbort as exc:
        raise _fail(str(exc)) from None

    console.print(ui.summary(spec))
    if not yes and not dry_run and not typer.confirm("Install now?", default=True):
        raise typer.Exit(1)

    runner = _runner(dry_run, f"install-{spec.name}")
    installer = Installer(spec, layout=_layout(), registry=registry, runner=runner)
    steps = installer.plan()
    for number, step in enumerate(steps, 1):
        title = f"[{number}/{len(steps)}] {step.title}"
        try:
            if dry_run:
                console.print(f"[bold]{title}[/bold]")
                step.action()
            else:
                with console.status(title + "…"):
                    step.action()
                console.print(f"[green]✓[/green] {title}")
        except CommandError as exc:
            console.print(f"[red]✗ {title} failed[/red]")
            if exc.output:
                console.print(Panel(exc.output, title="last output", border_style="red"))
            console.print(
                f"Full log: {runner.log_file}\n"
                "Fix the problem and run the same command again; finished steps are skipped."
            )
            raise typer.Exit(1) from None

    if dry_run:
        console.print("\n[yellow]Dry run: nothing was changed.[/yellow]")
        return
    inst = installer.instance
    if inst.mode == "local":
        _with_status("Starting Odoo…", lambda: operations.start(runner, inst))
    for warning in installer.warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")
    creds = installer.credentials
    console.print(
        Panel(
            f"[bold green]Odoo is ready:[/] {inst.url}\n\n"
            f"Login: [bold]admin[/]   Password: [bold]{creds.admin_password}[/]\n"
            f"Master password (database manager): stored in {inst.config_path}\n"
            f"Folder: {inst.instance_dir}\n\n"
            f"[dim]Save the password now, it is not shown again.\n"
            f"Manage it with: odooinstaller start|stop|logs|update|backup {inst.name}[/]",
            title=inst.label,
            expand=False,
        )
    )


def _with_status(message: str, fn):  # type: ignore[no-untyped-def]
    with console.status(message):
        return fn()


# --------------------------------------------------------- instance commands


@app.command("list")
def list_instances() -> None:
    """Show all instances on this machine."""
    registry = _registry()
    instances = sorted(registry.all().values(), key=lambda i: i.name)
    if not instances:
        console.print("No instances yet. Create one with: [bold]odooinstaller install[/]")
        return
    statuses = {i.name: operations.status(i) for i in instances}
    console.print(ui.instances_table(instances, statuses, registry.current()))


@app.command()
def info(name: Optional[str] = typer.Argument(None, help="Instance name")) -> None:
    """Show full details of an instance."""
    inst = _instance(name)
    ui.banner(inst)
    rows = {
        "Status": operations.status(inst),
        "URL": inst.url,
        "Web / websocket ports": f"{inst.http_port} / {inst.gevent_port}",
        "Database / user": f"{inst.db_name} / {inst.db_user}",
        "Python": inst.python,
        "Folder": inst.instance_dir,
        "Config": inst.config_path,
        "Log": inst.log_path,
        "Data (filestore)": inst.data_dir,
        "Community code": inst.community_dir,
        "Enterprise code": inst.enterprise_dir or "-",
        "Source": "private" if inst.own_source else "shared with same version",
        "Created": inst.created_at,
    }
    if inst.mode == "server":
        rows["Service"] = inst.service
    for key, value in rows.items():
        console.print(f"  [bold]{key:<22}[/] {value}")


@app.command()
def use(name: str = typer.Argument(..., help="Instance name")) -> None:
    """Set the current instance, used when a command is run without a name."""
    registry = _registry()
    inst = registry.get(name)
    if inst is None:
        raise _fail(f"no instance named {name!r}")
    registry.set_current(name)
    console.print(f"[green]✓[/] Now working with: [bold]{inst.label}[/]")


@app.command()
def start(name: Optional[str] = typer.Argument(None, help="Instance name")) -> None:
    """Start an instance."""
    inst = _instance(name)
    ui.banner(inst, "Starting")
    ok = _with_status("Starting Odoo…", lambda: operations.start(_runner(False, f"start-{inst.name}"), inst))
    if ok:
        console.print(f"[green]✓ Running:[/] {inst.url}")
    else:
        raise _fail(f"Odoo did not answer on port {inst.http_port}. Check: odooinstaller logs {inst.name}")


@app.command()
def stop(name: Optional[str] = typer.Argument(None, help="Instance name")) -> None:
    """Stop an instance."""
    inst = _instance(name)
    ui.banner(inst, "Stopping")
    operations.stop(_runner(False, f"stop-{inst.name}"), inst)
    console.print("[green]✓ Stopped[/]")


@app.command()
def restart(name: Optional[str] = typer.Argument(None, help="Instance name")) -> None:
    """Restart an instance."""
    inst = _instance(name)
    ui.banner(inst, "Restarting")
    ok = _with_status("Restarting…", lambda: operations.restart(_runner(False, f"restart-{inst.name}"), inst))
    if not ok:
        raise _fail(f"Odoo did not come back. Check: odooinstaller logs {inst.name}")
    console.print(f"[green]✓ Running:[/] {inst.url}")


@app.command()
def logs(
    name: Optional[str] = typer.Argument(None, help="Instance name"),
    lines: int = typer.Option(100, "--lines", "-n", help="Number of lines to show"),
    follow: bool = typer.Option(True, "--follow/--no-follow", "-f", help="Keep watching for new lines"),
) -> None:
    """Show an instance's log."""
    inst = _instance(name)
    ui.banner(inst, f"Log: {inst.log_path}")
    cmd = operations.logs_command(inst, lines, follow)
    os.execvp(cmd[0], cmd)


@app.command()
def shell(name: Optional[str] = typer.Argument(None, help="Instance name")) -> None:
    """Open an Odoo shell (Python with `env`) on the instance's database."""
    inst = _instance(name)
    ui.banner(inst, "Odoo shell — exit with Ctrl+D")
    cmd = operations.shell_command(inst)
    os.chdir(inst.instance_dir)
    os.execvp(cmd[0], cmd)


@app.command()
def update(
    name: Optional[str] = typer.Argument(None, help="Instance name"),
    modules: str = typer.Option("all", help="Modules to upgrade, comma-separated"),
    github_token: Optional[str] = typer.Option(
        None, envvar="ODOO_GITHUB_TOKEN", show_envvar=True, help="Needed to update Enterprise code"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen"),
) -> None:
    """Pull the latest Odoo code and upgrade modules (backs up server instances first)."""
    inst = _instance(name)
    _require_root_for(inst, "Updating")
    registry = _registry()
    ui.banner(inst, "Update code and modules")
    affected = operations.instances_sharing_source(registry, inst)
    if len(affected) > 1:
        names = ", ".join(i.name for i in affected)
        console.print(
            f"[yellow]⚠ The Odoo {inst.version} code is shared by: {names}. All of them will be updated.[/]"
        )
    if not yes and not dry_run:
        if inst.mode == "server":
            _confirm_by_typing(inst, "This updates a PRODUCTION instance (a backup is made first).")
        elif not typer.confirm("Continue?", default=False):
            raise typer.Exit(1)
    token = github_token
    if inst.enterprise_dir is not None and not token and sys.stdin.isatty() and not dry_run:
        token = (
            typer.prompt(
                "GitHub token for Enterprise (Enter to skip)", default="", hide_input=True, show_default=False
            )
            or None
        )
    runner = _runner(dry_run, f"update-{inst.name}")
    try:
        _with_status(
            "Updating…",
            lambda: operations.update(runner, registry, inst, github_token=token, modules=modules),
        )
    except CommandError as exc:
        console.print(Panel(exc.output or str(exc), title="update failed", border_style="red"))
        raise _fail(f"see the full log: {runner.log_file}") from None
    console.print(f"[green]✓ Updated:[/] {', '.join(i.name for i in affected)}")


@app.command()
def backup(
    name: Optional[str] = typer.Argument(None, help="Instance name"),
    to: Optional[Path] = typer.Option(None, "--to", help="Folder for the backup zip"),
) -> None:
    """Back up the database and filestore to a zip (Odoo's standard format)."""
    inst = _instance(name)
    _require_root_for(inst, "Backing up")
    ui.banner(inst, "Backup")
    runner = _runner(False, f"backup-{inst.name}")
    try:
        path = _with_status(
            "Backing up…", lambda: operations.backup(runner, inst, to or operations.default_backup_dir(inst))
        )
    except (CommandError, FileNotFoundError) as exc:
        raise _fail(f"backup failed: {exc}") from None
    console.print(f"[green]✓ Backup saved:[/] {path}")


@app.command()
def restore(
    archive: Path = typer.Argument(..., exists=True, dir_okay=False, help="Backup zip file"),
    name: Optional[str] = typer.Option(None, "--name", help="Instance to restore into"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the typed confirmation"),
) -> None:
    """Restore a backup zip into an instance (replaces its database and files)."""
    inst = _instance(name)
    _require_root_for(inst, "Restoring")
    ui.banner(inst, f"Restore from {archive}")
    if not yes:
        _confirm_by_typing(inst, f"This REPLACES the database '{inst.db_name}' and its files.")
    runner = _runner(False, f"restore-{inst.name}")
    try:
        _with_status("Restoring…", lambda: operations.restore(runner, inst, archive))
    except (CommandError, ValueError) as exc:
        raise _fail(f"restore failed: {exc}") from None
    console.print("[green]✓ Restored[/]")


@app.command()
def remove(
    name: Optional[str] = typer.Argument(None, help="Instance name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the typed confirmation"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted"),
) -> None:
    """Delete an instance: its database, files, service and web config. Shared code is kept."""
    inst = _instance(name)
    _require_root_for(inst, "Removing")
    ui.banner(inst, "Remove")
    if not yes and not dry_run:
        _confirm_by_typing(inst, f"This deletes '{inst.name}', its databases and all its files.")
    operations.remove(_runner(dry_run, f"remove-{inst.name}"), _registry(), _layout(), inst)
    console.print(
        "[yellow]Dry run: nothing was changed.[/]" if dry_run else f"[green]✓ Removed {inst.name}[/]"
    )


@app.command()
def doctor() -> None:
    """Check the system and every instance for problems."""
    problems = 0
    for check in operations.doctor(_registry()):
        icon = "[green]✓[/]" if check.ok else "[red]✗[/]"
        console.print(f"{icon} [bold]{check.subject:<12}[/] {check.message}")
        if not check.ok:
            problems += 1
            if check.fix:
                console.print(f"   [dim]fix: {check.fix}[/]")
    console.print(f"\n{problems} problem(s) found." if problems else "\n[green]Everything looks good.[/]")
    if problems:
        raise typer.Exit(1)


@app.command()
def versions() -> None:
    """List the Odoo versions available to install."""
    for v in _with_status("Reading Odoo versions from GitHub…", available_versions):
        console.print(f"  Odoo {v}")
