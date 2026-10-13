"""Shared terminal output: the instance banner, tables and the install summary."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .models import InstallSpec, Instance

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
        ("Demo data", "yes" if spec.demo else "no"),
    ]
    if spec.mode == "local":
        rows.append(("IDE", {"vscode": "VS Code", "pycharm": "PyCharm", "none": "none"}[spec.ide]))
    else:
        rows += [
            ("Domain", spec.domain or "none (access by IP and port)"),
            ("HTTPS", "Let's Encrypt" if spec.ssl else "no"),
            ("Workers", str(spec.workers)),
            ("Firewall", "enable (SSH + web only)" if spec.firewall else "leave unchanged"),
        ]
    if spec.own_source:
        rows.append(("Source code", "private copy for this instance"))
    table = Table.grid(padding=(0, 2))
    for key, value in rows:
        table.add_row(f"[bold]{key}[/]", value)
    return Panel(table, title="Install summary", expand=False)
