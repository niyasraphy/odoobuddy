"""Shared terminal output: the instance banner, tables and the install summary."""

from __future__ import annotations

import getpass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .models import IDE_NAMES, VSCODE_FAMILY, InstallSpec, Instance

console = Console()


def banner(instance: Instance, action: str = "") -> None:
    """Printed before every operation so it is always clear which Odoo is affected."""
    mode = "[bold white on red] PRODUCTION [/]" if instance.mode == "server" else "[green]LOCAL[/]"
    where = instance.domain or f"port {instance.http_port}"
    text = (
        f"[bold]▶ {instance.name}[/] · Odoo {instance.version} {instance.edition.title()} · {mode} · {where}"
    )
    if action:
        text += f"\n[dim]{action}[/dim]"
    console.print(Panel(text, expand=False, border_style="red" if instance.mode == "server" else "cyan"))


def instances_table(instances: list[Instance], statuses: dict[str, str], current: str | None) -> Table:
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("", "NAME", "VERSION", "EDITION", "MODE", "PORT", "URL", "STATUS"):
        table.add_column(col)
    for inst in instances:
        state = statuses.get(inst.name, "?")
        dot = "[green]● running[/]" if state == "running" else "[dim]○ stopped[/]"
        mode = "[red]server[/]" if inst.mode == "server" else "local"
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
        ("Mode", "Server (production)" if spec.mode == "server" else "Local (development)"),
        ("Ports", f"{spec.http_port} (web), {spec.gevent_port} (live chat / websocket)"),
    ]
    if spec.mode == "local":
        rows.append(("IDE", IDE_NAMES.get(spec.ide, "none")))
    else:
        rows += [
            ("Domain", spec.domain or "none (access by IP and port)"),
            ("HTTPS", "Let's Encrypt" if spec.ssl else "no"),
            ("Workers", str(spec.workers)),
            ("Firewall", "enable (SSH + web only)" if spec.firewall else "leave unchanged"),
        ]
    if spec.repos:
        rows.append(("Custom repos", "\n".join(f"{r.url} ({r.branch})" for r in spec.repos)))
    table = Table.grid(padding=(0, 2))
    for key, value in rows:
        table.add_row(f"[bold]{key}[/]", value)
    return Panel(table, title="Install summary", expand=False)


def _home(path: object) -> str:
    """Show paths under the home directory as ~/... (shorter, easier to read)."""
    from pathlib import Path

    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home) :] if text.startswith(home + "/") else text


def instance_details(instance: Instance) -> list[tuple[str, str]]:
    """Where everything about an instance lives, in the order people look for it."""
    server = instance.mode == "server"
    if server:
        runs_as = f"system user {instance.system_user} (service: {instance.service})"
        log = f"{instance.log_path}  (or: odoobuddy logs {instance.name})"
    else:
        runs_as = f"your user ({getpass.getuser()})"
        log = (
            f"{_home(instance.log_path)} when started with `odoobuddy start`; "
            "the IDE's Run window when started from the IDE"
        )
    rows = [
        ("URL", instance.url),
        (
            "Databases",
            f"create them at {instance.url}/web/database/manager  (PostgreSQL user: {instance.db_user})",
        ),
        ("Runs as", runs_as),
        ("Ports", f"{instance.http_port} web, {instance.gevent_port} websocket"),
        ("Folder", _home(instance.instance_dir)),
        ("Config file", _home(instance.config_path)),
        ("Log", log),
        (
            "Custom modules",
            f"{_home(instance.custom_dir / 'addons')}  (+ repos cloned into {_home(instance.custom_dir)})",
        ),
        ("Odoo code", _home(instance.community_dir)),
    ]
    if instance.enterprise_dir is not None:
        rows.append(("Enterprise code", _home(instance.enterprise_dir)))
    rows.append(("Python", f"{instance.python}  ({_home(instance.python_bin)})"))
    return rows


def ide_help(instance: Instance) -> str:
    """How to run and debug this local instance, for its editor or run mode."""
    if instance.mode != "local":
        return ""
    folder = _home(instance.instance_dir)
    name = instance.name
    attach = f'"Attach to Odoo ({name})"'
    no_editor = (
        "[bold]Without an editor[/]\n"
        f"  odoobuddy run {name}        in this terminal (Ctrl+C stops it)\n"
        f"  odoobuddy start|stop|logs {name}   in the background\n"
    )
    ide = instance.ide
    if ide in VSCODE_FAMILY:
        editor = IDE_NAMES[ide]
        return (
            f"[bold]{editor}[/] [dim](your installed {editor} is used as-is; only .vscode/ was written)[/]\n"
            f'  • Odoo starts by itself when the folder opens, in the terminal panel "Odoo: run {name}".\n'
            '    First time only: trust the folder and click "Allow" for automatic tasks.\n'
            f"  • Debug at any time: Run and Debug → {attach} → F5 (no restart needed).\n"
            f"  • Reopen later: odoobuddy open {name}   (folder: {folder})\n\n" + no_editor
        )
    if ide == "pycharm":
        return (
            "[bold]PyCharm[/] [dim](your installed PyCharm is used as-is; only .idea/ was written)[/]\n"
            f"  • Odoo starts by itself when the project opens (Startup Tasks), in the Run window.\n"
            '    First time only: click "Trust Project"; if asked for an interpreter, choose\n'
            f"    Existing → {_home(instance.python_bin)}\n"
            f'  • To debug: stop it (■) and click Debug (🐞) on "Odoo {instance.version} ({name})".\n'
            f"  • Reopen later: odoobuddy open {name}   (folder: {folder})\n\n" + no_editor
        )
    if ide == "zed":
        return (
            "[bold]Zed[/] [dim](your installed Zed is used as-is; only .zed/ was written)[/]\n"
            f'  • Start Odoo: Ctrl+Shift+P → "task: spawn" → "Odoo: run {name}"\n'
            "    (Zed cannot start tasks automatically when a folder opens)\n"
            f'  • Debug: Ctrl+Shift+P → "debugger: start" → {attach}\n'
            f"  • Reopen later: odoobuddy open {name}   (folder: {folder})\n\n" + no_editor
        )
    return (
        no_editor
        + f"  {folder}/start.sh        same as odoobuddy run\n"
        + f"  Use an editor later: odoobuddy open {name} --with vscode|pycharm|zed|cursor|...\n"
    )


def details_table(rows: list[tuple[str, str]]) -> Table:
    table = Table.grid(padding=(0, 2))
    for key, value in rows:
        table.add_row(f"[bold]{key}[/]", value)
    return table
