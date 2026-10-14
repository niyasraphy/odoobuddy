"""Editor projects for local instances, and opening the editor with Odoo running inside it.

Editors are never installed or changed: only project files are written into the instance
folder (.vscode/, .idea/ or .zed/).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sysconfig
from pathlib import Path

from . import render
from .ide import find_ide
from .models import IDE_NAMES, VSCODE_FAMILY, Instance
from .system import Runner

DEFAULT_COMMANDS = {
    "vscode": "code",
    "cursor": "cursor",
    "windsurf": "windsurf",
    "vscodium": "codium",
    "pycharm": "pycharm",
    "zed": "zed",
}


def configure(runner: Runner, instance: Instance) -> None:
    """Write the project files for ``instance.ide``."""
    ide, folder = instance.ide, instance.instance_dir
    if ide in VSCODE_FAMILY:
        vscode = folder / ".vscode"
        runner.write_file(vscode / "launch.json", render.vscode_launch(instance))
        runner.write_file(vscode / "tasks.json", render.vscode_tasks(instance))
        runner.write_file(vscode / "settings.json", render.vscode_settings(instance))
        runner.write_file(vscode / "extensions.json", render.vscode_extensions())
    elif ide == "pycharm":
        idea = folder / ".idea"
        runner.write_file(
            idea / "runConfigurations" / f"Odoo_{instance.name.replace('-', '_')}.xml",
            render.pycharm_run_config(instance),
        )
        runner.write_file(idea / "startup.xml", render.pycharm_startup(instance))
        # Project files: written once, never overwriting settings PyCharm or the user changed.
        for filename, content in (
            (f"{instance.name}.iml", render.pycharm_module(instance)),
            ("modules.xml", render.pycharm_modules(instance)),
            ("misc.xml", render.pycharm_misc(instance)),
        ):
            if not (idea / filename).exists() or runner.dry_run:
                runner.write_file(idea / filename, content)
    elif ide == "zed":
        zed = folder / ".zed"
        runner.write_file(zed / "tasks.json", render.zed_tasks(instance))
        runner.write_file(zed / "debug.json", render.zed_debug(instance))
        runner.write_file(zed / "settings.json", render.zed_settings(instance))


def site_packages(instance: Instance) -> Path:
    """The venv's site-packages, e.g. venv/lib/python3.12/site-packages."""
    lib = instance.venv_dir / "lib"
    found = sorted(lib.glob("python3*/site-packages"))
    if found:
        return found[-1]
    relative = Path(sysconfig.get_path("purelib")).relative_to(sysconfig.get_path("data"))
    return instance.venv_dir / relative


def install_debug_hook(runner: Runner, instance: Instance) -> None:
    """Let editors attach to a running Odoo on ``instance.debug_port`` (see render.DEBUG_HOOK)."""
    target = site_packages(instance)
    runner.write_file(target / "_odoobuddy_debug.py", render.DEBUG_HOOK)
    runner.write_file(target / "_odoobuddy_debug.pth", render.DEBUG_HOOK_PTH)


def debug_env(instance: Instance) -> dict[str, str]:
    return {"ODOOBUDDY_DEBUG_PORT": str(instance.debug_port)}


def has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def editor_command(instance: Instance) -> list[str] | None:
    """Command that opens the instance folder in its editor, or None if not installed."""
    launcher = find_ide(instance.ide)
    if launcher is None:
        return None
    return [*shlex.split(launcher), str(instance.instance_dir)]


def open_editor(instance: Instance) -> list[str]:
    """Open the editor detached from this terminal. Raises if it can't be opened."""
    if instance.ide not in IDE_NAMES:
        raise RuntimeError(f"{instance.name} has no editor configured")
    command = editor_command(instance)
    if command is None:
        name = IDE_NAMES[instance.ide]
        raise RuntimeError(f"{name} was not found on this computer")
    if not has_display():
        raise RuntimeError("no graphical session (e.g. connected over SSH)")
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=instance.instance_dir,
    )
    return command


def manual_open_hint(instance: Instance) -> str:
    """What to type to open the editor later."""
    command = editor_command(instance)
    if command is not None:
        return shlex.join(command)
    return f"{DEFAULT_COMMANDS.get(instance.ide, instance.ide)} {instance.instance_dir}"
