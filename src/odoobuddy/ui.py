"""Shared terminal output: the instance banner, tables and the install summary."""

from __future__ import annotations

import getpass
import os
import shlex

from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .ide import find_ide, install_hint
from .models import IDE_NAMES, PURPOSES, VSCODE_FAMILY, InstallSpec, Instance

console = Console()


# The welcome screen's logo (made with pyfiglet, fonts "ANSI Shadow" and "small"), and its
# colours: Odoo's purple fading to its teal, one colour per line.
LOGO = """\
 ██████╗ ██████╗  ██████╗  ██████╗ ██████╗ ██╗   ██╗██████╗ ██████╗ ██╗   ██╗
██╔═══██╗██╔══██╗██╔═══██╗██╔═══██╗██╔══██╗██║   ██║██╔══██╗██╔══██╗╚██╗ ██╔╝
██║   ██║██║  ██║██║   ██║██║   ██║██████╔╝██║   ██║██║  ██║██║  ██║ ╚████╔╝
██║   ██║██║  ██║██║   ██║██║   ██║██╔══██╗██║   ██║██║  ██║██║  ██║  ╚██╔╝
╚██████╔╝██████╔╝╚██████╔╝╚██████╔╝██████╔╝╚██████╔╝██████╔╝██████╔╝   ██║
 ╚═════╝ ╚═════╝  ╚═════╝  ╚═════╝ ╚═════╝  ╚═════╝ ╚═════╝ ╚═════╝    ╚═╝"""
LOGO_SMALL = r"""
         _          _            _    _
 ___  __| |___  ___| |__ _  _ __| |__| |_  _
/ _ \/ _` / _ \/ _ \ '_ \ || / _` / _` | || |
\___/\__,_\___/\___/_.__/\_,_\__,_\__,_|\_, |
                                        |__/"""[1:]
LOGO_COLOURS = ["#a45c96", "#975891", "#8a548c", "#5f7d8c", "#3a8a8f", "#017e84"]


def welcome(
    version: str,
    author: str,
    instances: list[Instance],
    statuses: dict[str, str],
    current: str | None,
    commands: list[tuple[str, str]],
    footer: str,
) -> Group:
    """The screen shown by `odoobuddy` alone: logo, instances (if any) and where to start."""
    width = console.width
    logo = LOGO if width >= 84 else LOGO_SMALL
    art = Text()
    for i, line in enumerate(logo.splitlines()):
        art.append(line + "\n", style=f"bold {LOGO_COLOURS[min(i, len(LOGO_COLOURS) - 1)]}")
    header = Group(
        art,
        Text.from_markup(
            "[bold]Install and manage Odoo instances, on your computer or on a server.[/]\n"
            f"[dim]by {escape(author)}[/]"
        ),
    )
    table = instances_table(instances, statuses, current) if instances else None
    menu = Table.grid(padding=(0, 2))
    for command, what in commands:
        menu.add_row("[bold #017e84]\u276f[/]", cmd(command), f"[dim]{escape(what)}[/]")
    # One width for every box (the widest content, within the terminal), so they line up.
    contents: list[RenderableType] = [header, menu, *([table] if table else [])]
    box_width = min(width, max(console.measure(c).maximum for c in contents) + 6)

    def box(content: RenderableType, title: str, colour: str, padding: tuple[int, int]) -> Panel:
        return Panel(
            content, title=title, title_align="left", border_style=colour, padding=padding, width=box_width
        )

    parts: list[RenderableType] = [
        box(header, f"[bold] Welcome to odoobuddy {escape(version)} [/]", BRAND, (1, 2))
    ]
    if table:
        parts.append(box(table, "[bold] Your instances [/]", "cyan", (0, 1)))
    if table:
        parts.append(box(menu, "[bold] Commands [/]", "green", (0, 1)))
    else:
        first = Group(Text.from_markup("You have no Odoo instances yet. Start with:\n"), menu)
        parts.append(box(first, "[bold] Get started [/]", "green", (0, 1)))
    parts.append(Text.from_markup(footer))
    return Group(*parts)


BRAND = "#875a7b"  # Odoo's purple, for frames and section lines


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


# The animation under a long install step: the letters of "odoo" jump one after another
# like a wave, then all together, rest, and start again. No words, just moves.
# ODOOBUDDY_NO_ANIMATION=1 turns it off.
WAVE_LETTERS = "odoo"
JUMP_SECONDS = 0.3  # how long each letter stays in the air
REST_SECONDS = 0.5
_CYCLE = JUMP_SECONDS * (len(WAVE_LETTERS) + 1) + REST_SECONDS  # the wave, one hop together, rest
_UP, _DOWN = "bold #35c4cb", f"bold {BRAND}"  # a letter in the air is teal, on the ground purple


def buddy_frame(elapsed: float) -> Text:
    """The wave ``elapsed`` seconds into a step: 3 lines (air, ground, shadows), indented."""
    t = elapsed % _CYCLE
    wave = len(WAVE_LETTERS) * JUMP_SECONDS
    if t < wave:
        up = {int(t // JUMP_SECONDS)}
    elif t < wave + JUMP_SECONDS:
        up = set(range(len(WAVE_LETTERS)))  # all together
    else:
        up = set()
    air, ground, shadows = Text("  "), Text("  "), Text("  ")
    for i, letter in enumerate(WAVE_LETTERS):
        jumping = i in up
        air.append(letter if jumping else " ", _UP)
        ground.append(" " if jumping else letter, _DOWN)
        shadows.append("·" if jumping else "▔", "dim")  # the shadow shrinks while it is in the air
        for line in (air, ground, shadows):
            line.append(" ")
    art = Text("\n").join([air, ground, shadows])
    art.rstrip()
    return art


def animations_on() -> bool:
    return not os.environ.get("ODOOBUDDY_NO_ANIMATION") and console.is_terminal


def section(title: str) -> None:
    """A titled line that separates the parts of a long screen (setup, installing, ...)."""
    console.print()
    console.print(Rule(f"[bold]{escape(title)}[/]", align="left", style=BRAND))


def summary(spec: InstallSpec) -> Panel:
    where = "on this server" if spec.mode == "server" else "on this computer"
    groups = [
        [
            ("Instance", f"[bold]{escape(spec.name)}[/]"),
            ("Odoo", f"{spec.version} {spec.edition.title()}"),
            ("Purpose", f"{PURPOSES[spec.role]} ({where})"),
        ],
        [("Ports", f"{spec.http_port} (web), {spec.gevent_port} (live chat / websocket)")],
    ]
    if spec.mode == "local":
        if spec.ide in IDE_NAMES:
            runs_in = IDE_NAMES[spec.ide] + (" (will be installed)" if spec.install_editor else "")
        else:
            runs_in = "a terminal" if spec.run_mode == "terminal" else "the background"
        groups[1].append(("Runs in", runs_in))
    else:
        groups[1] += [
            ("Domain", escape(spec.domain) if spec.domain else "none (access by IP and port)"),
            ("HTTPS", "Let's Encrypt" if spec.ssl else "no"),
        ]
        groups.append(
            [
                ("Workers", str(spec.workers)),
                ("Firewall", "enable (SSH + web only)" if spec.firewall else "leave unchanged"),
                (
                    "Backups",
                    "every night, the last 7 kept" if spec.daily_backups else "only when you run them",
                ),
            ]
        )
    if spec.repos:
        groups.append([("Custom repos", "\n".join(escape(f"{r.url} ({r.branch})") for r in spec.repos))])
    table = Table.grid(padding=(0, 3))
    for i, rows in enumerate(groups):
        if i:
            table.add_row("", "")  # a gap between the groups
        for key, value in rows:
            table.add_row(f"[bold cyan]{key}[/]", value)
    return Panel(
        table,
        title="[bold] Install summary [/]",
        title_align="left",
        border_style=BRAND,
        padding=(1, 2),
        expand=False,
    )


def install_command(spec: InstallSpec, *, as_root: bool = False) -> str:
    """The `odoobuddy install` command that repeats this install without questions, one option per line."""
    options = [["--purpose", spec.role], ["--version", spec.version], ["--edition", spec.edition]]
    options += [["--name", spec.name], ["--port", str(spec.http_port)]]
    if spec.mode == "local":
        options.append(["--run-with", spec.ide if spec.ide != "none" else spec.run_mode])
    else:
        options.append(["--domain", spec.domain] if spec.domain else ["--no-domain"])
        if spec.domain:
            options.append(["--ssl" if spec.ssl else "--no-ssl"])
            if spec.email:
                options.append(["--email", spec.email])
        options.append(["--workers", str(spec.workers)])
        options.append(["--firewall" if spec.firewall else "--no-firewall"])
        options.append(["--daily-backups" if spec.daily_backups else "--no-daily-backups"])
    options += [["--repo", f"{repo.url}#{repo.branch}"] for repo in spec.repos] or [["--no-repos"]]
    options.append(["--yes"])
    prefix = "sudo -E odoobuddy" if spec.mode == "server" and not as_root else "odoobuddy"
    lines = [f"{prefix} install"] + [f"    {shlex.join(option)}" for option in options]
    command = " \\\n".join(lines)
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
