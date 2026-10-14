"""The `odoobuddy` command."""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Group
from rich.markup import escape
from rich.panel import Panel

from . import __version__, editors, operations, repos, sources, ui
from . import pull as pull_mod
from .ide import detect_ides
from .installer import CHECK_MODULE, Installer, suggested_workers
from .layout import Layout
from .models import IDE_NAMES, Instance, RepoSpec
from .registry import Registry, RegistryError, resolve
from .system import (
    CommandError,
    Runner,
    SudoSession,
    cpu_count,
    is_root,
    memory_mb,
    os_release,
    port_in_use,
)
from .versions import available_versions
from .wizard import (
    QuestionaryPrompter,
    WizardAbort,
    WizardContext,
    answers_without,
    editable_fields,
    run_wizard,
)

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
        raise RegistryError("which instance? pass its name, e.g. odoobuddy start shop")
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


def _database(instance: Instance, db: Optional[str]) -> str:
    """Pick the database a command works on: --db, the only one, or ask."""
    try:
        databases = operations.list_databases(_runner(False, f"db-{instance.name}"), instance)
    except (CommandError, FileNotFoundError) as exc:
        raise _fail(f"cannot list databases of {instance.name}: {exc}") from None
    if db is not None:
        if db not in databases:
            known = ", ".join(databases) or "none yet"
            raise _fail(f"instance {instance.name} has no database {db!r} (databases: {known})")
        return db
    if not databases:
        raise _fail(f"{instance.name} has no database yet. Create one at {instance.url}/web/database/manager")
    if len(databases) == 1:
        return databases[0]
    if not sys.stdin.isatty():
        raise _fail(f"{instance.name} has several databases, choose one with --db: {', '.join(databases)}")
    try:
        return QuestionaryPrompter(console.print).select("Which database?", [(d, d) for d in databases])
    except WizardAbort:
        raise typer.Exit(1) from None


def _require_root_for(instance: Instance, action: str) -> None:
    if instance.mode == "server" and not is_root():
        raise _fail(f"{action} a server instance needs root: sudo odoobuddy {' '.join(sys.argv[1:])}")


def _confirm_by_typing(instance: Instance, warning: str) -> None:
    console.print(f"[yellow]⚠ {warning}[/yellow]")
    typed = typer.prompt("  Type the instance name to confirm")
    if typed.strip() != instance.name:
        raise _fail("name did not match, nothing was changed")


def _runner(dry_run: bool, log_name: str) -> Runner:
    log_dir = Path("/var/log/odoobuddy") if is_root() else _layout().state_dir / "logs"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Runner(console=console, dry_run=dry_run, log_file=log_dir / f"{log_name}-{stamp}.log")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"odoobuddy {__version__}")
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
    run_with: Optional[str] = typer.Option(
        None,
        "--run-with",
        help="Local only: vscode, cursor, windsurf, vscodium, pycharm, zed, terminal or background",
    ),
    ide: Optional[str] = typer.Option(None, hidden=True, help="Old name of --run-with (none = background)"),
    domain: Optional[str] = typer.Option(None, help="Server only: domain name, e.g. erp.example.com"),
    no_domain: bool = typer.Option(False, "--no-domain", help="Server only: no domain (no Nginx/SSL)"),
    ssl: Optional[bool] = typer.Option(None, "--ssl/--no-ssl", help="Server only: Let's Encrypt HTTPS"),
    email: Optional[str] = typer.Option(None, help="Server only: email for SSL notices"),
    workers: Optional[int] = typer.Option(None, help="Server only: number of Odoo workers"),
    firewall: Optional[bool] = typer.Option(None, "--firewall/--no-firewall", help="Server only: enable ufw"),
    github_token: Optional[str] = typer.Option(
        None,
        envvar="ODOO_GITHUB_TOKEN",
        show_envvar=True,
        help="Token with odoo/enterprise access (prefer the env var over typing it here)",
    ),
    repo: Optional[list[str]] = typer.Option(
        None, "--repo", help="Custom repository to clone, as URL or URL#BRANCH (repeatable)"
    ),
    no_repos: bool = typer.Option(False, "--no-repos", help="Do not ask about custom repositories"),
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
    choice = run_with or ide
    if choice is not None:
        if choice in IDE_NAMES:
            preset.update(ide=choice, run_mode="editor")
        elif choice in ("terminal", "background", "none"):
            preset.update(ide="none", run_mode="background" if choice == "none" else choice)
        else:
            raise _fail(f"--run-with must be one of: {', '.join([*IDE_NAMES, 'terminal', 'background'])}")
    if repo or no_repos:
        preset["repos"] = [
            RepoSpec(url=r.split("#", 1)[0], branch=r.split("#", 1)[1] if "#" in r else (odoo_version or ""))
            for r in repo or []
        ]
        if any(not r.branch for r in preset["repos"]):  # type: ignore[attr-defined]
            raise _fail("give --version, or a branch with --repo URL#BRANCH")
    for key, allowed in (
        ("edition", {"community", "enterprise"}),
        ("mode", {"local", "server"}),
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
        installed_ides=detect_ides(),
        check_repo=lambda url, branch, token: _with_status(
            "Checking repository…", lambda: sources.check_repo(Runner(console=console), url, branch, token)
        ),
        preset=preset,
    )
    try:
        spec = run_wizard(QuestionaryPrompter(console.print), ctx)
    except WizardAbort as exc:
        raise _fail(str(exc)) from None

    prompter = QuestionaryPrompter(console.print)
    while True:
        console.print(ui.summary(spec))
        if yes or dry_run:
            break
        try:
            choice = prompter.select(
                "What next?",
                [("install", "Install now"), ("edit", "Change a setting"), ("cancel", "Cancel")],
                default="install",
            )
            if choice == "install":
                break
            if choice == "cancel":
                console.print("Cancelled, nothing was changed.")
                raise typer.Exit(1)
            key = prompter.select("Which setting?", editable_fields(spec))
            ctx.preset = answers_without(spec, key)
            ctx.instances = registry.all()
            spec = run_wizard(prompter, ctx)
        except WizardAbort as exc:
            raise _fail(str(exc)) from None

    runner = _runner(dry_run, f"install-{spec.name}")
    installer = Installer(spec, layout=_layout(), registry=registry, runner=runner)
    steps = installer.plan()
    if not dry_run:
        console.print(f"[dim]Full log: {runner.log_file}[/dim]")
    try:
        sudo = SudoSession(runner).__enter__()
    except PermissionError as exc:
        raise _fail(str(exc)) from None
    try:
        for number, step in enumerate(steps, 1):
            title = f"[{number}/{len(steps)}] {step.title}"
            try:
                if dry_run:
                    console.print(f"[bold]{escape(title)}[/bold]")
                    step.action()
                else:
                    elapsed = _progress(title, step.action, runner)
                    console.print(f"[green]✓[/green] {escape(title)} [dim]({_duration(elapsed)})[/dim]")
            except CommandError as exc:
                console.print(f"[red]✗ {escape(title)} failed[/red]")
                if exc.output:
                    console.print(Panel(escape(exc.output), title="last output", border_style="red"))
                console.print(
                    f"Full log: {runner.log_file}\n"
                    "Fix the problem and run the same command again; finished steps are skipped."
                )
                raise typer.Exit(1) from None
    finally:
        sudo.__exit__()

    if dry_run:
        console.print("\n[yellow]Dry run: nothing was changed.[/yellow]")
        return
    inst = installer.instance
    after = _after_install(runner, inst)
    for warning in installer.warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")
    creds = installer.credentials
    first_db = (
        "[bold]Create your first database[/]\n"
        f"  1. {'Once Odoo is running, open' if inst.run_mode != 'background' else 'Open'} "
        f"{inst.url}/web/database/manager\n"
        f"  2. Master password: [bold]{creds.master_password}[/]\n"
        "  3. Choose the database name, language, country, demo data and admin password\n"
        f"  [dim](the master password is also saved as admin_passwd in {inst.config_path})[/]"
    )
    body = Group(
        f"[bold green]✓ Odoo {inst.version} is installed and working[/] "
        f"[dim](tested by installing '{CHECK_MODULE}' in a temporary database, then deleted)[/]\n",
        first_db,
        "",
        ui.details_table(ui.instance_details(inst)),
    )
    help_text = ui.ide_help(inst)
    if help_text:
        body = Group(body, "", help_text.rstrip())
    console.print(Panel(body, title=inst.label, expand=False))
    if after:
        console.print(after)
    console.print(
        f"[dim]Manage it: odoobuddy start|stop|restart|logs|shell|update|backup|info {inst.name}[/]"
    )


def _after_install(runner: Runner, inst: Instance) -> str:
    """Start Odoo the way the user chose. Returns a line to show under the summary."""
    if inst.mode == "server":
        return ""
    if inst.run_mode == "background":
        ok = _progress("Starting Odoo in the background", lambda: operations.start(runner, inst), runner)
        return "" if ok else f"[yellow]⚠ Odoo did not answer yet, check: odoobuddy logs {inst.name}[/]"
    if inst.run_mode == "terminal":
        return f"[bold]Start Odoo now:[/] odoobuddy run {inst.name}   [dim](Ctrl+C stops it)[/]"
    name = IDE_NAMES[inst.ide]
    try:
        editors.open_editor(inst)
    except RuntimeError as exc:
        return (
            f"[yellow]⚠ Could not open {name} ({exc}).[/] Open it yourself: "
            f"[bold]{escape(editors.manual_open_hint(inst))}[/]"
        )
    return f"[green]✓ Opened {name}[/] — Odoo runs inside it (see the steps above)."


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def _progress(title: str, action: Callable[[], object], runner: Runner) -> float:
    """Run ``action`` under a spinner that shows elapsed time and the latest output line."""
    started = time.monotonic()
    last_line = ""
    done = threading.Event()
    with console.status(escape(title) + "…") as status:

        def refresh() -> None:
            text = f"{escape(title)}… [dim]{_duration(time.monotonic() - started)}[/dim]"
            if last_line:
                text += f"\n  [dim]{escape(last_line[:110])}[/dim]"
            status.update(text)

        def on_output(line: str) -> None:
            nonlocal last_line
            last_line = line
            refresh()

        def tick() -> None:
            while not done.wait(1):
                refresh()

        threading.Thread(target=tick, daemon=True).start()
        runner.on_output = on_output
        try:
            action()
        finally:
            done.set()
            runner.on_output = None
    return time.monotonic() - started


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
        console.print("No instances yet. Create one with: [bold]odoobuddy install[/]")
        return
    statuses = {i.name: operations.status(i) for i in instances}
    console.print(ui.instances_table(instances, statuses, registry.current()))


@app.command()
def info(name: Optional[str] = typer.Argument(None, help="Instance name")) -> None:
    """Show full details of an instance."""
    inst = _instance(name)
    ui.banner(inst)
    rows = [("Status", operations.status(inst)), *ui.instance_details(inst)]
    if inst.mode == "server":
        rows.append(("Domain / SSL", f"{inst.domain or '-'} / {'yes' if inst.ssl else 'no'}"))
    rows.append(("Created", inst.created_at))
    console.print(ui.details_table(rows))
    help_text = ui.ide_help(inst)
    if help_text:
        console.print("\n" + help_text.rstrip())


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
    runner = _runner(False, f"start-{inst.name}")
    if inst.mode == "local":
        editors.ensure_debug_hook(runner, inst)
    with SudoSession(runner):
        ok = _with_status("Starting Odoo…", lambda: operations.start(runner, inst))
    if ok:
        console.print(f"[green]✓ Running:[/] {inst.url}")
    else:
        raise _fail(f"Odoo did not answer on port {inst.http_port}. Check: odoobuddy logs {inst.name}")


@app.command()
def stop(name: Optional[str] = typer.Argument(None, help="Instance name")) -> None:
    """Stop an instance."""
    inst = _instance(name)
    ui.banner(inst, "Stopping")
    runner = _runner(False, f"stop-{inst.name}")
    with SudoSession(runner):
        operations.stop(runner, inst)
    console.print("[green]✓ Stopped[/]")


@app.command()
def restart(name: Optional[str] = typer.Argument(None, help="Instance name")) -> None:
    """Restart an instance."""
    inst = _instance(name)
    ui.banner(inst, "Restarting")
    runner = _runner(False, f"restart-{inst.name}")
    with SudoSession(runner):
        ok = _with_status("Restarting…", lambda: operations.restart(runner, inst))
    if not ok:
        raise _fail(f"Odoo did not come back. Check: odoobuddy logs {inst.name}")
    console.print(f"[green]✓ Running:[/] {inst.url}")


@app.command("run")
def run_foreground(name: Optional[str] = typer.Argument(None, help="Instance name")) -> None:
    """Run Odoo in this terminal with live logs (Ctrl+C stops it). No editor needed."""
    inst = _instance(name)
    if inst.mode == "server":
        raise _fail(f"server instances run as a service: odoobuddy start|logs {inst.name}")
    if operations.status(inst) == operations.STATUS_RUNNING:
        raise _fail(
            f"{inst.name} is already running (in the background or an editor). "
            f"Stop it first: odoobuddy stop {inst.name}"
        )
    ui.banner(
        inst,
        f"Running in this terminal on {inst.url}  —  Ctrl+C to stop\n"
        f'Debugger can attach on port {inst.debug_port} ("Attach to Odoo")',
    )
    editors.ensure_debug_hook(_runner(False, f"run-{inst.name}"), inst)
    cmd, env = operations.run_command(inst)
    os.chdir(inst.instance_dir)
    os.execvpe(cmd[0], cmd, env)


@app.command("open")
def open_in_editor(
    name: Optional[str] = typer.Argument(None, help="Instance name"),
    with_editor: Optional[str] = typer.Option(
        None, "--with", help="Use (and configure) another editor: " + ", ".join(IDE_NAMES)
    ),
) -> None:
    """Open the instance in its editor; Odoo starts inside the editor."""
    inst = _instance(name)
    if inst.mode == "server":
        raise _fail("editors are for local instances")
    if with_editor is not None:
        if with_editor not in IDE_NAMES:
            raise _fail(f"--with must be one of: {', '.join(IDE_NAMES)}")
        inst.ide, inst.run_mode = with_editor, "editor"  # type: ignore[assignment]
        editors.configure(_runner(False, f"open-{inst.name}"), inst)
        _registry().update(inst)
    if inst.ide not in IDE_NAMES:
        raise _fail(f"{inst.name} has no editor yet, choose one: odoobuddy open {inst.name} --with vscode")
    ui.banner(inst, f"Opening in {IDE_NAMES[inst.ide]}")
    editors.ensure_debug_hook(_runner(False, f"open-{inst.name}"), inst)
    if operations.status(inst) == operations.STATUS_RUNNING:
        console.print("[yellow]Odoo is already running, so the editor could not start it (same port).[/]")
        if sys.stdin.isatty() and typer.confirm("Stop the running Odoo first?", default=True):
            runner = _runner(False, f"stop-{inst.name}")
            with SudoSession(runner):
                operations.stop(runner, inst)
    try:
        editors.open_editor(inst)
    except RuntimeError as exc:
        raise _fail(f"could not open the editor ({exc}). Run: {editors.manual_open_hint(inst)}") from None
    console.print(f"[green]✓ Opened {IDE_NAMES[inst.ide]}[/]\n" + ui.ide_help(inst).rstrip())


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
def shell(
    name: Optional[str] = typer.Argument(None, help="Instance name"),
    db: Optional[str] = typer.Option(None, "--db", help="Database (asked if the instance has several)"),
) -> None:
    """Open an Odoo shell (Python with `env`) on one of the instance's databases."""
    inst = _instance(name)
    database = _database(inst, db)
    ui.banner(inst, f"Odoo shell on database '{database}' — exit with Ctrl+D")
    cmd = operations.shell_command(inst, database)
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
    ui.banner(inst, "Update Odoo code, custom repositories and modules")
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
    warnings: list[str] = []
    try:
        _progress(
            "Updating",
            lambda: warnings.extend(operations.update(runner, inst, github_token=token, modules=modules)),
            runner,
        )
    except CommandError as exc:
        console.print(Panel(escape(exc.output or str(exc)), title="update failed", border_style="red"))
        raise _fail(f"see the full log: {runner.log_file}") from None
    for warning in warnings:
        console.print(f"[yellow]⚠ {escape(warning)}[/]")
    console.print(f"[green]✓ Updated {inst.name}[/]")


@app.command()
def backup(
    name: Optional[str] = typer.Argument(None, help="Instance name"),
    db: Optional[str] = typer.Option(None, "--db", help="Database (asked if the instance has several)"),
    to: Optional[Path] = typer.Option(None, "--to", help="Folder for the backup zip"),
) -> None:
    """Back up a database and its filestore to a zip (Odoo's standard format)."""
    inst = _instance(name)
    _require_root_for(inst, "Backing up")
    database = _database(inst, db)
    ui.banner(inst, f"Backup of database '{database}'")
    runner = _runner(False, f"backup-{inst.name}")
    try:
        path = _with_status(
            "Backing up…",
            lambda: operations.backup(runner, inst, database, to or operations.default_backup_dir(inst)),
        )
    except (CommandError, FileNotFoundError) as exc:
        raise _fail(f"backup failed: {exc}") from None
    console.print(f"[green]✓ Backup saved:[/] {path}")


@app.command()
def restore(
    archive: Path = typer.Argument(..., exists=True, dir_okay=False, help="Backup zip file"),
    name: Optional[str] = typer.Option(None, "--name", help="Instance to restore into"),
    db: str = typer.Option(..., "--db", help="Database to restore into (created or replaced)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the typed confirmation"),
) -> None:
    """Restore a backup zip into a database of an instance (replacing it if it exists)."""
    inst = _instance(name)
    _require_root_for(inst, "Restoring")
    ui.banner(inst, f"Restore {archive} into database '{db}'")
    if not yes and db in operations.list_databases(_runner(False, f"restore-{inst.name}"), inst):
        _confirm_by_typing(inst, f"Database '{db}' exists and will be REPLACED, with its files.")
    runner = _runner(False, f"restore-{inst.name}")
    try:
        _with_status("Restoring…", lambda: operations.restore(runner, inst, archive, db))
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
    runner = _runner(dry_run, f"remove-{inst.name}")
    with SudoSession(runner):
        operations.remove(runner, _registry(), _layout(), inst)
    console.print(
        "[yellow]Dry run: nothing was changed.[/]" if dry_run else f"[green]✓ Removed {inst.name}[/]"
    )


@app.command()
def clone(
    url: str = typer.Argument(..., help="Repository URL (https://... or git@...)"),
    branch: Optional[str] = typer.Argument(None, help="Branch (default: the instance's Odoo version)"),
    instance_name: Optional[str] = typer.Option(None, "--instance", "-i", help="Instance name"),
    folder: Optional[str] = typer.Option(None, "--dir", help="Folder name under custom/"),
    token: Optional[str] = typer.Option(
        None, envvar="ODOO_REPO_TOKEN", show_envvar=True, help="Access token for a private https repo"
    ),
    restart_now: Optional[bool] = typer.Option(None, "--restart/--no-restart", help="Restart if running"),
) -> None:
    """Clone a custom module repository into custom/ and add it to the addons path.

    Also works as:  odoobuddy INSTANCE clone URL [BRANCH]
    """
    inst = _instance(instance_name)
    _require_root_for(inst, "Cloning into")
    branch = branch or inst.version
    ui.banner(inst, f"Clone {url} ({branch}) into {inst.custom_dir}")
    runner = _runner(False, f"clone-{inst.name}")
    result = _with_status("Checking repository…", lambda: sources.check_repo(runner, url, branch, token))
    if not result.ok and result.needs_auth and not token and sys.stdin.isatty():
        console.print(f"[yellow]{result.message}[/]")
        token = (
            typer.prompt("Access token (Enter to cancel)", default="", hide_input=True, show_default=False)
            or None
        )
        if token:
            result = sources.check_repo(runner, url, branch, token)
    if not result.ok:
        raise _fail(result.message)
    cloned: list[Path] = []
    try:
        _progress(
            "Cloning",
            lambda: cloned.append(repos.clone_repo(runner, inst, url, branch, token=token, folder=folder)),
            runner,
        )
        path = cloned[0]
        if (
            _progress(
                "Installing Python requirements",
                lambda: repos.install_requirements(runner, inst, path),
                runner,
            )
            and (path / "requirements.txt").is_file()
        ):
            console.print("[green]✓[/] Installed the repository's Python requirements")
    except (CommandError, FileExistsError, ValueError) as exc:
        raise _fail(f"clone failed: {getattr(exc, 'output', '') or exc}") from None
    added = repos.module_folders(path)
    repos.refresh_addons_path(runner, inst)
    console.print(f"[green]✓ Cloned into[/] {path}")
    if added:
        console.print("[green]✓ Added to addons_path:[/] " + ", ".join(str(p) for p in added))
    else:
        console.print("[yellow]⚠ No Odoo modules (__manifest__.py) found in this repository yet.[/]")
    if operations.status(inst) == operations.STATUS_RUNNING:
        if restart_now is None:
            restart_now = sys.stdin.isatty() and typer.confirm(
                "Restart Odoo to load the new addons path?", default=True
            )
        if restart_now:
            with SudoSession(runner):
                _with_status("Restarting…", lambda: operations.restart(runner, inst))
            console.print("[green]✓ Restarted[/] — update the Apps list in Odoo to see new modules")


@app.command()
def pull(
    source: str = typer.Argument(
        ..., help="Server and instance: USER@HOST:INSTANCE (or USER@HOST with --db)"
    ),
    into: Optional[str] = typer.Option(None, "--into", "-i", help="Local instance to copy into"),
    db: Optional[str] = typer.Option(None, "--db", help="Database on the server (asked if several)"),
    as_db: Optional[str] = typer.Option(None, "--as", help="Local database name (default: <db>_<date>)"),
    remote_filestore: Optional[str] = typer.Option(
        None, help="Server folder that contains filestore/<db> (only for non-odoobuddy servers)"
    ),
    ssh_port: Optional[int] = typer.Option(None, help="SSH port, if not 22"),
    no_neutralize: bool = typer.Option(False, "--no-neutralize", help="Keep emails, crons, payments ON"),
    no_filestore: bool = typer.Option(False, "--no-filestore", help="Copy only the database"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Replace an existing local database without asking"),
) -> None:
    """Copy a production database and its files from a server to a local instance, made safe.

    Also works as:  odoobuddy LOCAL_INSTANCE pull USER@HOST:INSTANCE
    """
    inst = _instance(into)
    if inst.mode != "local":
        raise _fail("pull copies INTO a local instance; choose one with --into")
    try:
        origin = pull_mod.parse_source(source)
        remote = pull_mod.Remote(origin.host, ssh_port=ssh_port)
        ui.banner(inst, f"Pull from {source}")
        _with_status(f"Connecting to {origin.host}…", remote.connect)

        def choose(databases: list[str]) -> str:
            if not sys.stdin.isatty():
                raise pull_mod.PullError(
                    f"several databases on the server, pick one with --db: {', '.join(databases)}"
                )
            return QuestionaryPrompter(console.print).select("Which database?", [(d, d) for d in databases])

        plan = _with_status(
            "Reading the server…",
            lambda: pull_mod.plan_pull(
                remote,
                origin,
                inst,
                db=db,
                local_db=as_db,
                remote_filestore=remote_filestore,
                choose_db=choose,
            ),
        )
    except (pull_mod.PullError, WizardAbort) as exc:
        raise _fail(str(exc)) from None
    runner = _runner(False, f"pull-{inst.name}")
    existing = operations.list_databases(runner, inst)
    if plan.local_db in existing and not yes:
        _confirm_by_typing(inst, f"Local database '{plan.local_db}' exists and will be REPLACED.")
    if no_neutralize:
        console.print("[bold yellow]⚠ Not neutralizing: this copy may SEND EMAILS and run payments/crons.[/]")

    def step(title: str, action: Callable[[], object]) -> object:
        holder: list[object] = []
        elapsed = _progress(title, lambda: holder.append(action()), runner)
        console.print(f"[green]✓[/green] {escape(title)} [dim]({_duration(elapsed)})[/dim]")
        return holder[0] if holder else None

    try:
        result = pull_mod.execute_pull(
            runner,
            remote,
            inst,
            plan,
            neutralize=not no_neutralize,
            copy_filestore=not no_filestore,
            step=step,
        )
    except (pull_mod.PullError, CommandError) as exc:
        console.print(
            Panel(escape(getattr(exc, "output", "") or str(exc)), title="pull failed", border_style="red")
        )
        raise _fail(f"see the full log: {runner.log_file}") from None
    sizes = f"database {pull_mod.human_size(result.dump_size)}"
    if result.filestore_size:
        sizes += f", files {pull_mod.human_size(result.filestore_size)}"
    console.print(
        Panel(
            f"[bold green]✓ Copied[/] {plan.remote_db} → [bold]{result.local_db}[/] ({sizes})\n"
            + (
                "Neutralized: outgoing mail, scheduled actions and integrations are off.\n"
                if result.neutralized
                else "[yellow]Not neutralized.[/]\n"
            )
            + f"Open: {inst.url}/web?db={result.local_db}\n"
            "[dim]Users and passwords are the same as on the server.[/]",
            title=inst.label,
            expand=False,
        )
    )


@app.command("repos")
def list_repositories(name: Optional[str] = typer.Argument(None, help="Instance name")) -> None:
    """List the custom repositories of an instance."""
    inst = _instance(name)
    ui.banner(inst, f"Custom repositories in {inst.custom_dir}")
    found = repos.list_repos(_runner(False, f"repos-{inst.name}"), inst)
    if not found:
        console.print(f"None yet. Add one with: odoobuddy {inst.name} clone URL [BRANCH]")
        return
    for repo in found:
        console.print(f"[bold]{repo.name}[/]  {repo.branch}  [dim]{repo.commit}[/]\n  {repo.url}")


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


# Commands that take the instance as their first argument, and ones that take --instance.
_NAME_FIRST = {
    "run",
    "open",
    "info",
    "use",
    "start",
    "stop",
    "restart",
    "logs",
    "shell",
    "update",
    "backup",
    "remove",
    "repos",
}
_NAME_OPTION = {"clone": "--instance", "restore": "--name", "pull": "--into"}


def rewrite_args(args: list[str], instance_names: set[str]) -> list[str]:
    """Allow ``odoobuddy INSTANCE COMMAND ...`` as well as ``odoobuddy COMMAND INSTANCE``."""
    if len(args) >= 2 and args[0] in instance_names:
        name, command, rest = args[0], args[1], args[2:]
        if command in _NAME_FIRST:
            return [command, name, *rest]
        if command in _NAME_OPTION:
            return [command, *rest, _NAME_OPTION[command], name]
    return args


def run() -> None:
    """Console-script entry point."""
    try:
        names = set(_registry().all())
    except RegistryError:
        names = set()
    app(args=rewrite_args(sys.argv[1:], names))
