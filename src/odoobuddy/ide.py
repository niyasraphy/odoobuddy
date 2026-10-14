"""Finding IDEs that are already installed. odoobuddy never installs or changes them;
it only writes project files (.idea/ or .vscode/) into the instance folder."""

from __future__ import annotations

import glob
import shlex
import shutil
from pathlib import Path

_COMMANDS = {
    "vscode": ["code", "code-insiders"],
    "cursor": ["cursor"],
    "windsurf": ["windsurf"],
    "vscodium": ["codium"],
    "pycharm": ["pycharm", "pycharm-community", "pycharm-professional", "charm"],
}
_PATTERNS = {
    "vscode": [
        "/snap/bin/code",
        "/usr/share/code/bin/code",
        "/var/lib/flatpak/exports/bin/com.visualstudio.code",
        "~/.local/share/flatpak/exports/bin/com.visualstudio.code",
    ],
    "cursor": [
        "/usr/share/cursor/cursor",
        "/opt/Cursor/cursor",
        "/opt/cursor/cursor",
        "~/Applications/[Cc]ursor*.AppImage",
        "~/[Cc]ursor*.AppImage",
    ],
    "windsurf": ["/usr/share/windsurf/windsurf", "/opt/Windsurf/windsurf", "/opt/windsurf/windsurf"],
    "vscodium": [
        "/snap/bin/codium",
        "/usr/share/codium/bin/codium",
        "/var/lib/flatpak/exports/bin/com.vscodium.codium",
        "~/.local/share/flatpak/exports/bin/com.vscodium.codium",
    ],
    "pycharm": [
        "/snap/bin/pycharm-*",
        "~/.local/share/JetBrains/Toolbox/scripts/pycharm*",
        "~/pycharm*/bin/pycharm.sh",
        "~/Applications/pycharm*/bin/pycharm.sh",
        "/opt/pycharm*/bin/pycharm.sh",
        "/opt/pycharm*/bin/pycharm",
        "~/.local/share/JetBrains/Toolbox/apps/*pycharm*/bin/pycharm.sh",
        "/var/lib/flatpak/exports/bin/com.jetbrains.PyCharm-*",
        "~/.local/share/flatpak/exports/bin/com.jetbrains.PyCharm-*",
    ],
}


# Every installation method (tarball, Toolbox, snap, deb, flatpak) adds a menu entry.
_DESKTOP_DIRS = [
    "~/.local/share/applications",
    "/usr/share/applications",
    "/var/lib/snapd/desktop/applications",
    "/var/lib/flatpak/exports/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
]
_DESKTOP_FILES = {
    "vscode": ["code.desktop", "code_code.desktop", "com.visualstudio.code.desktop"],
    "cursor": ["cursor.desktop", "cursor*.desktop"],
    "windsurf": ["windsurf.desktop"],
    "vscodium": ["codium.desktop", "codium_codium.desktop", "com.vscodium.codium.desktop"],
    "pycharm": ["jetbrains-pycharm*.desktop", "pycharm*.desktop", "com.jetbrains.PyCharm*.desktop"],
}


def _from_desktop_entry(ide: str, dirs: list[str]) -> str | None:
    for folder in dirs:
        for pattern in _DESKTOP_FILES.get(ide, []):
            for entry in sorted(glob.glob(str(Path(folder).expanduser() / pattern))):
                try:
                    lines = Path(entry).read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for line in lines:
                    if line.startswith("Exec="):
                        parts = shlex.split(line[len("Exec=") :])
                        # Drop field codes such as %f / %U; keep the program and its fixed args.
                        command = [p for p in parts if not p.startswith("%")]
                        if command:
                            return shlex.join(command)
    return None


def find_ide(ide: str, *, desktop_dirs: list[str] | None = None) -> str | None:
    """Return a command that opens ``ide``, or None if it doesn't seem to be installed."""
    for command in _COMMANDS.get(ide, []):
        if path := shutil.which(command):
            return path
    for pattern in _PATTERNS.get(ide, []):
        matches = sorted(glob.glob(str(Path(pattern).expanduser())))
        if matches:
            return matches[-1]  # newest version when several are installed
    return _from_desktop_entry(ide, _DESKTOP_DIRS if desktop_dirs is None else desktop_dirs)


def detect_ides() -> dict[str, str | None]:
    return {ide: find_ide(ide) for ide in _COMMANDS}
