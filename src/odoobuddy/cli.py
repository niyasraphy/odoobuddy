"""The `odoobuddy` command."""

from __future__ import annotations

import contextlib
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

from . import AUTHOR, DOCS_URL, REPO_URL, SUPPORT_EMAIL, __version__, editors, operations, repos, sources, ui
from . import pull as pull_mod
from .ide import detect_ides, editor_install, find_ide, install_hint
from .installer import (
    CHECK_MODULE,
    MIN_FREE_DISK_GB,
    Installer,
    processes_in_use,
    resource_warnings,
    suggested_workers,
    worker_limit,
)
from .layout import Layout
from .models import IDE_NAMES, InstallSpec, Instance, RepoSpec, backup_unit_for
from .registry import Registry, RegistryError, resolve
from .system import (
    CommandError,
    Runner,
    SudoSession,
    apt_busy,
    command_exists,
    completion_installed,
    cpu_count,
    detect_mode,
    free_disk_gb,
    is_root,
    login_shell,
    memory_available_mb,
    memory_mb,
    os_release,
    port_in_use,
    prune_old_logs,
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
    add_completion=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = ui.console

SUPPORTED_OS = {("ubuntu", "jammy"), ("ubuntu", "noble"), ("debian", "bookworm")}


def _complete_names(incomplete: str) -> list[str]:
    """Tab completion for instance names (odoobuddy start <TAB>)."""
    try:
        names = Registry(Layout.default()).all()
    except RegistryError:
        return []
    return sorted(n for n in names if n.startswith(incomplete))


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
    """Production: type the instance name to confirm. Test and development: a yes/no question."""
    console.print(f"[yellow]⚠ {warning}[/yellow]")
    if not instance.is_production:
        if not typer.confirm("  Continue?", default=False):
            raise _fail("nothing was changed")
        return
    typed = typer.prompt("  Type the instance name to confirm")
    if typed.strip() != instance.name:
        raise _fail("name did not match, nothing was changed")


LOGS_KEPT_DAYS = 30  # nightly backups write a log each night; older ones are deleted


def _runner(dry_run: bool, log_name: str) -> Runner:
    log_dir = Path("/var/log/odoobuddy") if is_root() else _layout().state_dir / "logs"
    if not dry_run:
        prune_old_logs(log_dir, LOGS_KEPT_DAYS)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Runner(console=console, dry_run=dry_run, log_file=log_dir / f"{log_name}-{stamp}.log")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"odoobuddy {__version__} · by {AUTHOR} · {ui.link(REPO_URL)}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """With no command, shows a welcome with the instances and the commands to start with."""
    if ctx.invoked_subcommand is None:
        _welcome()


def _system_check() -> None:
    """What this machine has, shown before the first question, so problems show up early."""
    release = os_release()
    name = release.get("PRETTY_NAME", "This system")
    lines = []
    if (release.get("ID"), release.get("VERSION_CODENAME")) in SUPPORTED_OS:
        lines.append(f"[green]✓[/] {name}")
    else:
        lines.append(f"[yellow]⚠ {name} is not tested[/] [dim](supported: Ubuntu 22.04/24.04, Debian 12)[/]")
    total, free = memory_mb(), memory_available_mb()
    if total:
        lines.append(f"[green]✓[/] {cpu_count()} CPUs, {total / 1024:.1f} GB RAM ({free / 1024:.1f} GB free)")
    disk = free_disk_gb(_layout().server_root if is_root() else _layout().local_home)
    mark = "[green]✓[/]" if disk >= MIN_FREE_DISK_GB else "[yellow]⚠[/]"
    lines.append(f"{mark} {disk:.0f} GB free disk space")
    if detect_mode()[0] == "server" and port_in_use(80) and not command_exists("nginx"):
        lines.append(
            "[yellow]⚠ Port 80 is used by another web server (Apache?)[/] "
            "[dim]Odoo on a server needs Nginx there; stop the other one first.[/]"
        )
    busy = apt_busy()
    if busy:
        lines.append(
            f"[yellow]⚠ Ubuntu is installing updates ({busy})[/] [dim]the install will wait for them[/]"
        )
    instances = sorted(_registry().all().values(), key=lambda i: i.name)
    if instances:
        listed = ", ".join(f"{i.name} ({i.role}, Odoo {i.version})" for i in instances)
        lines.append(f"[dim]Already here: {listed}[/]")
    console.print("\n".join(lines) + "\n")


def _check_resources(
    spec: InstallSpec, registry: Registry, prompter: QuestionaryPrompter, *, yes: bool, dry_run: bool
) -> None:
    """Warn when the machine looks too small for one more instance; the user decides."""
    warnings = resource_warnings(
        spec,
        available_mb=memory_available_mb(),
        free_disk_gb=free_disk_gb(_layout().instance_dir(spec.mode, spec.name)),
    )
    if not warnings:
        return
    others = sorted(name for name in registry.all() if name != spec.name)
    text = "\n".join(f"• {escape(w)}" for w in warnings)
    if others:
        text += f"\n\n[dim]Already on this machine: {len(others)} instance(s): {', '.join(others)}[/]"
    console.print(Panel(text, title="⚠ This machine may be too small", border_style="yellow", expand=False))
    if dry_run:
        return
    if yes:
        console.print("[yellow]Continuing because of --yes.[/]")
        return
    try:
        go = prompter.confirm("Install anyway, at your own risk?", default=False)
    except WizardAbort:
        go = False
    if not go:
        console.print("Cancelled, nothing was changed.")
        raise typer.Exit(1)


def _welcome() -> None:
    registry = _registry()
    instances = sorted(registry.all().values(), key=lambda i: i.name)
    statuses = {i.name: operations.status(i) for i in instances}
    if instances:
        commands = [
            ("odoobuddy install", "install another Odoo instance"),
            ("odoobuddy status NAME", "is it running, and where to open it"),
            ("odoobuddy start|stop|restart NAME", "control an instance"),
            ("odoobuddy logs NAME", "watch its log"),
            ("odoobuddy info NAME", "paths, ports and configuration"),
        ]
    else:
        commands = [("odoobuddy install", "answer a few questions, and Odoo is set up for you")]
    commands += [("odoobuddy help", "all commands"), ("odoobuddy support", "questions, bugs and contact")]
    footer = f"[dim]Docs:[/] {ui.link(DOCS_URL)}  [dim]·  ⭐[/] {ui.link(REPO_URL)}"
    console.print(ui.welcome(__version__, AUTHOR, instances, statuses, registry.current(), commands, footer))


# ------------------------------------------------------------------ install


@app.command()
def install(
    name: Optional[str] = typer.Option(None, help="Instance name, e.g. shop"),
    odoo_version: Optional[str] = typer.Option(None, "--version", "-v", help="Odoo version, e.g. 19.0"),
    edition: Optional[str] = typer.Option(None, help="community or enterprise"),
    purpose: Optional[str] = typer.Option(
        None, help="production or test (on this server), or development (on this computer)"
    ),
    mode: Optional[str] = typer.Option(None, help="local or server (older name: server = production)"),
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
    daily_backups: Optional[bool] = typer.Option(
        None, "--daily-backups/--no-daily-backups", help="Server only: back up every database each night"
    ),
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
    _system_check()

    preset: dict[str, object] = {}
    for key, value in {
        "name": name,
        "version": odoo_version,
        "edition": edition,
        "mode": mode,
        "purpose": purpose,
        "http_port": port,
        "ssl": ssl,
        "email": email,
        "workers": workers,
        "firewall": firewall,
        "daily_backups": daily_backups,
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
        ("purpose", {"production", "test", "development"}),
    ):
        if key in preset and preset[key] not in allowed:
            raise _fail(f"--{key} must be one of: {', '.join(sorted(allowed))}")

    registry = _registry()
    detected_mode, detected_reason = detect_mode()
    ctx = WizardContext(
        instances=registry.all(),
        versions=lambda: _with_status("Reading Odoo versions from GitHub…", available_versions),
        port_in_use=port_in_use,
        check_enterprise=lambda token: _with_status(
            "Checking GitHub access…", lambda: sources.check_enterprise_access(token)
        ),
        is_root=is_root() or dry_run,
        suggested_workers=suggested_workers(cpu_count(), memory_mb()),
        max_workers=worker_limit(cpu_count(), memory_mb(), processes_in_use(registry.all().values())),
        detected_mode=detected_mode,
        detected_reason=detected_reason,
        available_mb=memory_available_mb(),
        free_disk_gb=free_disk_gb(_layout().server_root),
        interactive=not yes and sys.stdin.isatty(),
        installed_ides=detect_ides(),
        find_ide=find_ide,
        editor_install=editor_install,
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
    first_preset = dict(preset)
    while True:
        console.print(ui.summary(spec))
        if not yes:
            console.print(
                f"[dim]To repeat this install from a script:[/]\n[dim]{escape(ui.install_command(spec))}[/]"
            )
        if yes:
            break
        if not sys.stdin.isatty():
            if dry_run:
                break
            raise _fail("no terminal to confirm the install: add --yes to install from a script")
        try:
            go = "Show what would be done (dry run)" if dry_run else "Install now"
            choice = prompter.select(
                "What next?",
                [
                    ("install", go),
                    ("edit", "Change a setting"),
                    ("restart", "Start over"),
                    ("cancel", "Cancel"),
                ],
                default="install",
            )
            if choice == "install":
                break
            if choice == "cancel":
                console.print("Cancelled, nothing was changed.")
                raise typer.Exit(1)
            if choice == "restart":
                ctx.preset = dict(first_preset)
                ctx.instances = registry.all()
                spec = run_wizard(prompter, ctx)
                continue
            key = prompter.select("Which setting?", editable_fields(spec))
            ctx.preset = answers_without(spec, key)
            ctx.instances = registry.all()
            spec = run_wizard(prompter, ctx)
        except WizardAbort as exc:
            raise _fail(str(exc)) from None

    _check_resources(spec, registry, prompter, yes=yes, dry_run=dry_run)
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
    manager = ui.link(f"{inst.url}/web/database/manager")
    first_db = (
        ui.heading("Create your first database") + "\n"
        f"  1. {'Once Odoo is running, open' if inst.run_mode != 'background' else 'Open'} {manager}\n"
        f"  2. Master password: {ui.secret(creds.master_password)}\n"
        "  3. Choose the database name, language, country, demo data and admin password\n"
        "  [dim](the master password is also saved as admin_passwd in[/] "
        f"{ui.path(inst.config_path)}[dim])[/]"
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
    console.print(Panel(body, title=f"[bold]{escape(inst.label)}[/]", border_style="green", expand=False))
    if after:
        console.print(after)
    console.print(
        f"[dim]Manage it: odoobuddy start|stop|restart|logs|shell|update|backup|info {inst.name}[/]"
    )
    console.print(f"\n[dim]odoobuddy by {AUTHOR} · if it helped, a ⭐ on GitHub is welcome: {REPO_URL}[/]")
    if not yes and sys.stdin.isatty():
        _offer_completion(prompter)


def _offer_completion(prompter: QuestionaryPrompter) -> None:
    """After an install, offer Tab completion for odoobuddy, unless it is on already."""
    shell = login_shell()
    # Under sudo from another account, it would be set up for root, not for that user.
    if shell is None or completion_installed(shell) or os.environ.get("SUDO_USER"):
        return
    try:
        if not prompter.confirm(
            "Turn on Tab completion for odoobuddy commands and instance names?", default=True
        ):
            console.print(f"[dim]Later:[/] {ui.cmd('odoobuddy --install-completion')}")
            return
        from typer._completion_shared import install as install_completion

        _, path = install_completion(shell=shell, prog_name="odoobuddy")
    except WizardAbort:
        return
    except Exception as exc:  # Typer's installer is internal API: never fail the install over it
        later = ui.cmd("odoobuddy --install-completion")
        console.print(f"[yellow]Could not turn on Tab completion ({escape(str(exc))}).[/] Try: {later}")
        return
    rc = {"bash": "~/.bashrc", "zsh": "~/.zshrc"}.get(shell)
    now = f"[dim], or here after:[/] {ui.cmd('source ' + rc)}" if rc else ""
    console.print(
        f"[green]✓ Tab completion is on[/] [dim]({ui.path(path)}). It works in new terminals[/]{now}"
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
    if find_ide(inst.ide) is None:
        return (
            f"[yellow]⚠ {name} is not installed, so it was not opened.[/] To install it: "
            f"[bold]{escape(install_hint(inst.ide))}[/]\n  then run: [bold]odoobuddy open {inst.name}[/]"
            f"   [dim](or without an editor: odoobuddy run {inst.name})[/]"
        )
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


QUIET_HINT_AFTER = 120  # seconds without output before the spinner explains what to do


def _is_apt_lock_wait(line: str) -> bool:
    return "Could not get lock" in line or "Waiting for cache lock" in line


def _progress(title: str, action: Callable[[], object], runner: Runner) -> float:
    """Run ``action`` under a spinner that shows elapsed time and the latest output line."""
    started = last_output = time.monotonic()
    last_line = ""
    done = threading.Event()
    with console.status(escape(title) + "…") as status:

        def refresh() -> None:
            now = time.monotonic()
            text = f"{escape(title)}… [dim]{_duration(now - started)}[/dim]"
            if last_line:
                text += f"\n  [dim]{escape(last_line[:110])}[/dim]"
            if _is_apt_lock_wait(last_line):
                text += (
                    "\n  [yellow]Waiting for Ubuntu's automatic updates to finish (usually a few minutes)[/]"
                )
            elif now - last_output > QUIET_HINT_AFTER:
                text += (
                    f"\n  [yellow]No output for {_duration(now - last_output)}: probably still working.[/] "
                    f"[dim]Full log:[/] {ui.path(runner.log_file)}\n"
                    "  [dim]Ctrl+C is safe: run the same command again to continue where it stopped.[/]"
                )
            status.update(text)

        def on_output(line: str) -> None:
            nonlocal last_line, last_output
            last_line, last_output = line, time.monotonic()
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
def info(
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
) -> None:
    """Show full details of an instance."""
    inst = _instance(name)
    ui.banner(inst)
    rows = [("Status", operations.status(inst)), *ui.instance_details(inst)]
    if inst.mode == "server":
        rows.append(("Domain / SSL", f"{inst.domain or '-'} / {'yes' if inst.ssl else 'no'}"))
        backups = operations.default_backup_dir(inst)
        if operations.backups_scheduled(_layout(), inst):
            rows.append(("Backups", f"every night, in {ui.path(backups)}"))
        else:
            turn_on = ui.cmd(f"odoobuddy backup {inst.name} --nightly")
            rows.append(("Backups", f"not scheduled  [dim](turn on:[/] {turn_on}[dim])[/]"))
    rows.append(("Created", inst.created_at.replace("T", " ").replace("+00:00", " UTC")))
    console.print(ui.details_table(rows))
    help_text = ui.ide_help(inst)
    if help_text:
        console.print("\n" + help_text.rstrip())


@app.command()
def status(
    name: Optional[str] = typer.Argument(
        None, help="Instance name (all instances if left out)", autocompletion=_complete_names
    ),
) -> None:
    """Is Odoo running? Every instance, or just one."""
    if name is None:
        list_instances()
        return
    inst = _instance(name)
    if operations.status(inst) == operations.STATUS_RUNNING:
        console.print(f"[bold]{inst.name}[/]  [green]● running[/]  {ui.link(inst.url)}")
        return
    console.print(f"[bold]{inst.name}[/]  [dim]○ stopped[/]")
    console.print(f"  Start it: {ui.cmd(f'odoobuddy start {inst.name}')}")
    if inst.mode == "server":
        console.print(f"  Why it stopped: {ui.cmd(f'journalctl -u {inst.service} -n 50')}")


@app.command()
def use(name: str = typer.Argument(..., help="Instance name", autocompletion=_complete_names)) -> None:
    """Set the current instance, used when a command is run without a name."""
    registry = _registry()
    inst = registry.get(name)
    if inst is None:
        raise _fail(f"no instance named {name!r}")
    registry.set_current(name)
    console.print(f"[green]✓[/] Now working with: [bold]{inst.label}[/]")


@app.command()
def start(
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
) -> None:
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
def stop(
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
) -> None:
    """Stop an instance."""
    inst = _instance(name)
    ui.banner(inst, "Stopping")
    runner = _runner(False, f"stop-{inst.name}")
    with SudoSession(runner):
        operations.stop(runner, inst)
    console.print("[green]✓ Stopped[/]")


@app.command()
def restart(
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
) -> None:
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
def run_foreground(
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
) -> None:
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


def _get_editor(inst: Instance) -> None:
    """The instance's editor is missing: install it (if we can and the user agrees), or wait
    while the user installs it. Fails if it is still not there."""
    name = IDE_NAMES[inst.ide]
    give_up = (
        f"{name} is not installed. Install it ({install_hint(inst.ide)}), then: odoobuddy open {inst.name}"
    )
    console.print(f"[yellow]⚠ {name} is not installed on this computer.[/]")
    if not sys.stdin.isatty():
        raise _fail(give_up)
    how = editor_install(inst.ide)
    if how is not None and typer.confirm(f"Install {name} now ({how.how})?", default=True):
        runner = _runner(False, f"install-{inst.ide}")
        try:
            with SudoSession(runner) if how.sudo else contextlib.nullcontext():
                _progress(f"Installing {name}", lambda: runner.run(how.command, sudo=how.sudo), runner)
        except (CommandError, PermissionError) as exc:
            console.print(f"[red]Installing {name} failed:[/] {escape(str(exc))}")
    while find_ide(inst.ide) is None:
        console.print(f"Please {escape(install_hint(inst.ide))}")
        if not typer.confirm(f"Have you installed {name}? Check again", default=True):
            raise _fail(give_up)
    console.print(f"[green]✓ {name} is installed[/]")


@app.command("open")
def open_in_editor(
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
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
    if find_ide(inst.ide) is None:
        _get_editor(inst)
    try:
        editors.open_editor(inst)
    except RuntimeError as exc:
        raise _fail(f"could not open the editor ({exc}). Run: {editors.manual_open_hint(inst)}") from None
    console.print(f"[green]✓ Opened {IDE_NAMES[inst.ide]}[/]\n" + ui.ide_help(inst).rstrip())


@app.command()
def logs(
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
    lines: int = typer.Option(100, "--lines", "-n", help="Number of lines to show"),
    follow: bool = typer.Option(True, "--follow/--no-follow", "-f", help="Keep watching for new lines"),
) -> None:
    """Show an instance's log."""
    inst = _instance(name)
    action = f"Log: {inst.log_path}"
    if follow:
        action += "\nShowing new lines as they come - Ctrl+C to stop (--no-follow to just print)"
    ui.banner(inst, action)
    if not inst.log_path.exists() or inst.log_path.stat().st_size == 0:
        state = "does not exist yet" if not inst.log_path.exists() else "is empty"
        console.print(f"[yellow]⚠ The log {state}: Odoo has not written anything to it.[/]")
        if inst.mode == "server":
            console.print(
                f"  Is Odoo running? {ui.cmd(f'systemctl status {inst.service}')}  "
                f"[dim]startup errors:[/] {ui.cmd(f'journalctl -u {inst.service} -n 50')}"
            )
        else:
            console.print(f"  Is Odoo running? {ui.cmd(f'odoobuddy start {inst.name}')}")
    cmd = operations.logs_command(inst, lines, follow)
    os.execvp(cmd[0], cmd)


@app.command()
def shell(
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
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
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
    modules: str = typer.Option("all", help="Modules to upgrade, comma-separated"),
    github_token: Optional[str] = typer.Option(
        None, envvar="ODOO_GITHUB_TOKEN", show_envvar=True, help="Needed to update Enterprise code"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen"),
) -> None:
    """Pull the latest Odoo code and upgrade modules (backs up production instances first)."""
    inst = _instance(name)
    _require_root_for(inst, "Updating")
    ui.banner(inst, "Update Odoo code, custom repositories and modules")
    if not yes and not dry_run:
        if inst.is_production:
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
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
    db: Optional[str] = typer.Option(None, "--db", help="Database (asked if the instance has several)"),
    all_dbs: bool = typer.Option(False, "--all", help="Back up every database of the instance"),
    to: Optional[Path] = typer.Option(None, "--to", help="Folder for the backup zip"),
    keep: Optional[int] = typer.Option(
        None, "--keep", min=1, help="Then delete older backups, keeping this many per database"
    ),
    nightly: Optional[bool] = typer.Option(
        None,
        "--nightly/--no-nightly",
        help=f"Server: back up every database each night, keeping the last {operations.BACKUPS_KEPT} "
        "(or --keep); --no-nightly stops it",
    ),
) -> None:
    """Back up a database and its filestore to a zip (Odoo's standard format)."""
    inst = _instance(name)
    _require_root_for(inst, "Backing up")
    dest = to or operations.default_backup_dir(inst)
    if nightly is not None:
        _schedule_nightly(inst, nightly, keep or operations.BACKUPS_KEPT, dest, custom_dest=to is not None)
        return
    if all_dbs:
        databases = operations.list_databases(_runner(False, f"db-{inst.name}"), inst)
        if not databases:
            console.print(f"{inst.name} has no databases yet: nothing to back up.")
            return
    else:
        databases = [_database(inst, db)]
    ui.banner(inst, f"Backup of {', '.join(repr(d) for d in databases)} to {dest}")
    runner = _runner(False, f"backup-{inst.name}")
    failed = []
    for database in databases:
        try:
            path = _with_status(
                f"Backing up {database}…", lambda d=database: operations.backup(runner, inst, d, dest)
            )
        except (CommandError, FileNotFoundError, OSError) as exc:
            console.print(f"[red]✗ {database}: backup failed: {escape(str(exc))}[/]")
            failed.append(database)
            continue
        console.print(f"[green]✓ Backup saved:[/] {ui.path(path)}")
        if keep:
            for old in operations.prune_backups(inst, database, dest, keep):
                console.print(f"  [dim]deleted old backup {old.name}[/]")
    if failed:
        raise typer.Exit(1)


def _schedule_nightly(inst: Instance, on: bool, keep: int, dest: Path, *, custom_dest: bool) -> None:
    if inst.mode != "server":
        raise _fail("nightly backups are for server instances (they use a systemd timer)")
    layout = _layout()
    runner = _runner(False, f"backup-schedule-{inst.name}")
    try:
        if on:
            operations.schedule_backups(runner, layout, inst, keep, dest.resolve() if custom_dest else None)
        else:
            operations.unschedule_backups(runner, layout, inst)
    except CommandError as exc:
        raise _fail(f"could not change the backup schedule: {exc}") from None
    if on:
        console.print(
            f"[green]✓ Every database of {inst.name} is backed up each night (around 02:30), "
            f"the last {keep} kept[/] in {ui.path(dest)}\n"
            f"  [dim]Next run:[/] {ui.cmd('systemctl list-timers ' + backup_unit_for(inst.name) + '.timer')}"
        )
    else:
        console.print(f"[green]✓ Nightly backups of {inst.name} stopped.[/] Existing backups are kept.")


@app.command()
def restore(
    archive: Path = typer.Argument(..., exists=True, dir_okay=False, help="Backup zip file"),
    name: Optional[str] = typer.Option(
        None, "--name", help="Instance to restore into", autocompletion=_complete_names
    ),
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
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
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
    instance_name: Optional[str] = typer.Option(
        None, "--instance", "-i", help="Instance name", autocompletion=_complete_names
    ),
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
    into: Optional[str] = typer.Option(
        None, "--into", "-i", help="Local instance to copy into", autocompletion=_complete_names
    ),
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
def list_repositories(
    name: Optional[str] = typer.Argument(None, help="Instance name", autocompletion=_complete_names),
) -> None:
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
def support() -> None:
    """Where to get help, report a bug, or contact the author."""
    rows = [
        ("Documentation", ui.link(DOCS_URL)),
        (
            "Questions and bugs",
            f"{ui.link(REPO_URL + '/issues')}  [dim](search first, someone may have asked)[/]",
        ),
        ("Email", f"{SUPPORT_EMAIL}  [dim](security problems and private matters)[/]"),
        ("Source code", ui.link(REPO_URL)),
    ]
    console.print(f"[bold]odoobuddy[/] [dim]{__version__}[/] · by {AUTHOR}\n")
    console.print(ui.details_table(rows))
    console.print(
        f"\n[dim]When reporting a problem, include the output of[/] {ui.cmd('odoobuddy doctor')}"
        "[dim], and for a failed install the end of its log:[/] "
        f"{ui.path('/var/log/odoobuddy/')} [dim]on servers,[/] {ui.path('~/.local/share/odoobuddy/logs/')} "
        "[dim]locally.[/]"
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


# Commands that take the instance as their first argument, and ones that take --instance.
_NAME_FIRST = {
    "status",
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
    """Allow ``odoobuddy INSTANCE COMMAND ...`` as well as ``odoobuddy COMMAND INSTANCE``.

    Also ``odoobuddy help [COMMAND]``, as people type it, for ``odoobuddy [COMMAND] --help``.
    """
    if args and args[0] == "help" and "help" not in instance_names:
        return [*args[1:2], "--help"]
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
