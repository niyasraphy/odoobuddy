"""Shared terminal output: the instance banner, tables and the install summary."""

from __future__ import annotations

import getpass
import shlex

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .ide import find_ide, install_hint
from .models import IDE_NAMES, PURPOSES, VSCODE_FAMILY, InstallSpec, Instance

console = Console()


# How each purpose is labelled, so production always stands out.
PURPOSE_STYLE = {
    "production": ("PRODUCTION", "red"),
    "test": ("TEST", "dark_orange"),
    "development": ("DEV", "green"),
}


def banner(instance: Instance, action: str = "") -> None:
    """Printed before every operation so it is always clear which Odoo is affected."""
    label, colour = PURPOSE_STYLE[instance.role]
    where = instance.domain or f"port {instance.http_port}"
    text = (
        f"[bold]▶ {instance.name}[/] · Odoo {instance.version} {instance.edition.title()} · "
        f"[bold white on {colour}] {label} [/] · {where}"
    )
    if action:
        text += f"\n[dim]{action}[/dim]"
    console.print(Panel(text, expand=False, border_style=colour))


def instances_table(instances: list[Instance], statuses: dict[str, str], current: str | None) -> Table:
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("", "NAME", "VERSION", "EDITION", "PURPOSE", "PORT", "URL", "STATUS"):
        table.add_column(col)
    for inst in instances:
        state = statuses.get(inst.name, "?")
        dot = "[green]● running[/]" if state == "running" else "[dim]○ stopped[/]"
        label, colour = PURPOSE_STYLE[inst.role]
        mode = f"[{colour}]{label.lower()}[/]"
        table.add_row(
            "▶" if inst.name == current else "",
            inst.name,
            inst.version,
            inst.edition.title(),
            mode,
            str(inst.http_port),
            inst.url,
            dot,
        )
    return table


def summary(spec: InstallSpec) -> Panel:
    rows = [
        ("Instance", spec.name),
        ("Odoo", f"{spec.version} {spec.edition.title()}"),
        (
            "Purpose",
            f"{PURPOSES[spec.role]} ({'on this server' if spec.mode == 'server' else 'on this computer'})",
        ),
        ("Ports", f"{spec.http_port} (web), {spec.gevent_port} (live chat / websocket)"),
    ]
    if spec.mode == "local":
        if spec.ide in IDE_NAMES:
            editor = IDE_NAMES[spec.ide] + (" (will be installed)" if spec.install_editor else "")
            rows.append(("Runs in", editor))
        else:
            rows.append(("Runs in", "a terminal" if spec.run_mode == "terminal" else "the background"))
    else:
        rows += [
            ("Domain", spec.domain or "none (access by IP and port)"),
            ("HTTPS", "Let's Encrypt" if spec.ssl else "no"),
            ("Workers", str(spec.workers)),
            ("Firewall", "enable (SSH + web only)" if spec.firewall else "leave unchanged"),
            ("Backups", "every night, the last 7 kept" if spec.daily_backups else "only when you run them"),
        ]
    if spec.repos:
        rows.append(("Custom repos", "\n".join(f"{r.url} ({r.branch})" for r in spec.repos)))
    table = Table.grid(padding=(0, 2))
    for key, value in rows:
        table.add_row(f"[bold]{key}[/]", value)
    return Panel(table, title="Install summary", expand=False)


def install_command(spec: InstallSpec) -> str:
    """The `odoobuddy install` command that repeats this install without questions."""
    args = ["--purpose", spec.role, "--version", spec.version, "--edition", spec.edition, "--name", spec.name]
    args += ["--port", str(spec.http_port)]
    if spec.mode == "local":
        args += ["--run-with", spec.ide if spec.ide != "none" else spec.run_mode]
    else:
        args += ["--domain", spec.domain] if spec.domain else ["--no-domain"]
        if spec.domain:
            args.append("--ssl" if spec.ssl else "--no-ssl")
            if spec.email:
                args += ["--email", spec.email]
        args += ["--workers", str(spec.workers)]
        args.append("--firewall" if spec.firewall else "--no-firewall")
        args.append("--daily-backups" if spec.daily_backups else "--no-daily-backups")
    for repo in spec.repos:
        args += ["--repo", f"{repo.url}#{repo.branch}"]
    if not spec.repos:
        args.append("--no-repos")
    prefix = "sudo -E odoobuddy" if spec.mode == "server" else "odoobuddy"
    command = f"{prefix} install {shlex.join(args)} --yes"
    if spec.edition == "enterprise":
        command = "export ODOO_GITHUB_TOKEN=...  # your token\n" + command
    return command


def _home(path: object) -> str:
    """Show paths under the home directory as ~/... (shorter, easier to read)."""
    from pathlib import Path

    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home) :] if text.startswith(home + "/") else text


# One colour per kind of thing, used everywhere, so people learn where to look:
# links cyan, files and folders blue, commands to type green, keys like keycaps,
# secrets yellow, section headings magenta.
def link(url: str) -> str:
    return f"[bold bright_cyan underline link={url}]{escape(url)}[/]"


def path(value: object) -> str:
    return f"[bright_blue]{escape(_home(value))}[/]"


def cmd(text: str) -> str:
    return f"[green]{escape(text)}[/]"


def keys(text: str) -> str:
    return f"[reverse] {escape(text)} [/]"


def item(text: str) -> str:
    """A menu entry or button name the user clicks."""
    return f'[bold]"{escape(text)}"[/]'


def secret(text: str) -> str:
    return f"[bold yellow]{escape(text)}[/]"


def heading(text: str) -> str:
    return f"[bold bright_magenta]{escape(text)}[/]"


def instance_details(instance: Instance) -> list[tuple[str, str]]:
    """Where everything about an instance lives, in the order people look for it."""
    server = instance.mode == "server"
    if server:
        runs_as = f"system user [bold]{instance.system_user}[/] (service: [bold]{instance.service}[/])"
        log = f"{path(instance.log_path)}  [dim](or:[/] {cmd(f'odoobuddy logs {instance.name}')}[dim])[/]"
    else:
        runs_as = f"your user ([bold]{getpass.getuser()}[/])"
        log = (
            f"{path(instance.log_path)} [dim]when started with[/] {cmd('odoobuddy start')}[dim]; "
            "the IDE's Run window when started from the IDE[/]"
        )
    rows = [
        ("URL", link(instance.url)),
        (
            "Databases",
            f"create them at {link(instance.url + '/web/database/manager')}  "
            f"[dim](PostgreSQL user:[/] [bold]{instance.db_user}[/][dim])[/]",
        ),
        ("Runs as", runs_as),
        (
            "Ports",
            f"[bold]{instance.http_port}[/] web, [bold]{instance.gevent_port}[/] websocket",
        ),
        ("Folder", path(instance.instance_dir)),
        ("Config file", path(instance.config_path)),
        ("Log", log),
        (
            "Custom modules",
            f"{path(instance.custom_dir / 'addons')}  "
            f"[dim](+ repos cloned into[/] {path(instance.custom_dir)}[dim])[/]",
        ),
        ("Odoo code", path(instance.community_dir)),
    ]
    if instance.enterprise_dir is not None:
        rows.append(("Enterprise code", path(instance.enterprise_dir)))
    rows.append(("Python", f"[bold]{instance.python}[/]  [dim]([/]{path(instance.python_bin)}[dim])[/]"))
    return rows


def ide_help(instance: Instance) -> str:
    """How to run and debug this local instance, for its editor or run mode."""
    if instance.mode != "local":
        return ""
    folder = path(instance.instance_dir)
    name = instance.name
    attach = item(f"Attach to Odoo ({name})")
    no_editor = (
        heading("Without an editor") + "\n"
        f"  {cmd(f'odoobuddy run {name}')}        "
        f"[dim]in this terminal ([/]{keys('Ctrl+C')}[dim] stops it)[/]\n"
        f"  {cmd(f'odoobuddy start|stop|logs {name}')}   [dim]in the background[/]\n"
    )
    ide = instance.ide
    reopen = f"  • Reopen later: {cmd(f'odoobuddy open {name}')}   [dim](folder:[/] {folder}[dim])[/]\n\n"

    def header(folder_name: str) -> str:
        editor = IDE_NAMES[ide]
        if find_ide(ide):
            return (
                f"{heading(editor)} [dim](your installed {editor} is used as-is; "
                f"only {folder_name} was written)[/]\n"
            )
        return (
            f"{heading(editor)} [bold yellow]not installed yet[/] [dim](only {folder_name} was written)[/]\n"
            f"  • Install it: {cmd(install_hint(ide))}\n"
        )

    if ide in VSCODE_FAMILY:
        return (
            header(".vscode/")
            + f"  • Odoo [bold green]starts by itself[/] when the folder opens, in the terminal panel "
            f"{item(f'Odoo: run {name}')}.\n"
            f"    [dim]First time only: trust the folder and click[/] {item('Allow')} "
            "[dim]for automatic tasks.[/]\n"
            f"  • Debug at any time: {item('Run and Debug')} → {attach} → {keys('F5')} "
            "[dim](no restart needed)[/]\n" + reopen + no_editor
        )
    if ide == "pycharm":
        return (
            header(".idea/")
            + "  • Odoo [bold green]starts by itself[/] when the project opens (Startup Tasks), "
            "in the Run window.\n"
            f"    [dim]First time only: click[/] {item('Trust Project')}[dim]; if asked for an interpreter, "
            "choose[/]\n"
            f"    [dim]Existing →[/] {path(instance.python_bin)}\n"
            f"  • To debug: stop it (■) and click {item('Debug')} 🐞 on "
            f"{item(f'Odoo {instance.version} ({name})')}\n" + reopen + no_editor
        )
    if ide == "zed":
        return (
            header(".zed/") + f"  • Start Odoo: {keys('Ctrl+Shift+P')} → {item('task: spawn')} → "
            f"{item(f'Odoo: run {name}')}\n"
            "    [dim](Zed cannot start tasks automatically when a folder opens)[/]\n"
            f"  • Debug: {keys('Ctrl+Shift+P')} → {item('debugger: start')} → {attach}\n" + reopen + no_editor
        )
    return (
        no_editor
        + f"  {path(instance.instance_dir / 'start.sh')}        [dim]same as[/] {cmd('odoobuddy run')}\n"
        + "  [dim]Use an editor later:[/] "
        + cmd(f"odoobuddy open {name} --with vscode|pycharm|zed|cursor|...")
        + "\n"
    )


def details_table(rows: list[tuple[str, str]]) -> Table:
    table = Table.grid(padding=(0, 2))
    for key, value in rows:
        table.add_row(f"[bold cyan]{key}[/]", value)
    return table
