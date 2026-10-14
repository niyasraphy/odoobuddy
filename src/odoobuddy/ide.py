"""Finding IDEs that are already installed. odoobuddy never installs or changes them;
it only writes project files (.idea/ or .vscode/) into the instance folder."""

from __future__ import annotations

import glob
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

# Where to download each editor, for the ones odoobuddy cannot install itself.
DOWNLOAD_URLS = {
    "vscode": "https://code.visualstudio.com/download",
    "cursor": "https://cursor.com/downloads",
    "windsurf": "https://windsurf.com/download",
    "vscodium": "https://vscodium.com",
    "pycharm": "https://www.jetbrains.com/pycharm/download",
    "zed": "https://zed.dev/download",
}
# Official snap packages (Ubuntu and other systems with snap).
SNAP_PACKAGES = {"vscode": "code", "vscodium": "codium", "pycharm": "pycharm"}
ZED_INSTALL = "curl -fsSL https://zed.dev/install.sh | sh"


@dataclass(frozen=True)
class EditorInstall:
    command: list[str]
    sudo: bool
    how: str  # shown to the user, e.g. "with snap, needs your password"


def editor_install(ide: str) -> EditorInstall | None:
    """How odoobuddy can install ``ide`` here (only when the user asks), or None."""
    if ide == "zed":
        # Zed's official installer: goes into ~/.local, no admin rights.
        return EditorInstall(["sh", "-c", ZED_INSTALL], False, "official installer from zed.dev, no password")
    package = SNAP_PACKAGES.get(ide)
    if package and shutil.which("snap"):
        return EditorInstall(
            ["snap", "install", package, "--classic"], True, "with snap, needs your password"
        )
    return None


def install_hint(ide: str) -> str:
    """What the user can do to install ``ide`` themselves."""
    url = DOWNLOAD_URLS.get(ide, "")
    if ide == "zed":
        return ZED_INSTALL
    if ide in SNAP_PACKAGES and shutil.which("snap"):
        return f"sudo snap install {SNAP_PACKAGES[ide]} --classic   (or download: {url})"
    return f"download and install it from {url}"


_COMMANDS = {
    "vscode": ["code", "code-insiders"],
    "cursor": ["cursor"],
    "windsurf": ["windsurf"],
    "vscodium": ["codium"],
    "pycharm": ["pycharm", "pycharm-community", "pycharm-professional", "charm"],
    "zed": ["zed", "zeditor", "zedit"],
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
        "/snap/bin/pycharm*",
        "~/.local/share/JetBrains/Toolbox/scripts/pycharm*",
        "~/pycharm*/bin/pycharm.sh",
        "~/Applications/pycharm*/bin/pycharm.sh",
        "/opt/pycharm*/bin/pycharm.sh",
        "/opt/pycharm*/bin/pycharm",
        "~/.local/share/JetBrains/Toolbox/apps/*pycharm*/bin/pycharm.sh",
        "/var/lib/flatpak/exports/bin/com.jetbrains.PyCharm-*",
        "~/.local/share/flatpak/exports/bin/com.jetbrains.PyCharm-*",
    ],
    "zed": [
        "~/.local/bin/zed",
        "~/.local/zed.app/bin/zed",
        "/var/lib/flatpak/exports/bin/dev.zed.Zed",
        "~/.local/share/flatpak/exports/bin/dev.zed.Zed",
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
    "zed": ["dev.zed.Zed.desktop", "zed.desktop"],
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
