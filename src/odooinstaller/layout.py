"""Where files live, for local and server instances."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import Mode


@dataclass(frozen=True)
class Layout:
    """All filesystem locations. Tests build one pointing into a temp dir."""

    local_home: Path
    state_dir: Path
    server_root: Path
    server_etc: Path
    server_conf_dir: Path
    server_log_dir: Path
    server_data_dir: Path
    systemd_dir: Path
    nginx_dir: Path
    logrotate_dir: Path

    @classmethod
    def default(cls) -> Layout:
        if root := os.environ.get("ODOOINSTALLER_ROOT"):
            return cls.under(Path(root))
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return cls(
            local_home=Path(os.environ.get("ODOOINSTALLER_HOME", Path.home() / "odoo")),
            state_dir=data_home / "odooinstaller",
            server_root=Path("/opt/odooinstaller"),
            server_etc=Path("/etc/odooinstaller"),
            server_conf_dir=Path("/etc/odoo"),
            server_log_dir=Path("/var/log/odoo"),
            server_data_dir=Path("/var/lib/odoo"),
            systemd_dir=Path("/etc/systemd/system"),
            nginx_dir=Path("/etc/nginx"),
            logrotate_dir=Path("/etc/logrotate.d"),
        )

    @classmethod
    def under(cls, root: Path) -> Layout:
        """A layout rooted in ``root`` (used by tests and --root)."""
        return cls(
            local_home=root / "home" / "odoo",
            state_dir=root / "home" / ".local" / "share" / "odooinstaller",
            server_root=root / "opt" / "odooinstaller",
            server_etc=root / "etc" / "odooinstaller",
            server_conf_dir=root / "etc" / "odoo",
            server_log_dir=root / "var" / "log" / "odoo",
            server_data_dir=root / "var" / "lib" / "odoo",
            systemd_dir=root / "etc" / "systemd" / "system",
            nginx_dir=root / "etc" / "nginx",
            logrotate_dir=root / "etc" / "logrotate.d",
        )

    def sources_root(self, mode: Mode) -> Path:
        return self.local_home / ".sources" if mode == "local" else self.server_root / "sources"

    def shared_source(self, mode: Mode, version: str, repo: str) -> Path:
        return self.sources_root(mode) / version / repo

    def instance_dir(self, mode: Mode, name: str) -> Path:
        return self.local_home / name if mode == "local" else self.server_root / "instances" / name

    def config_path(self, mode: Mode, name: str) -> Path:
        if mode == "local":
            return self.instance_dir(mode, name) / "odoo.conf"
        return self.server_conf_dir / f"{name}.conf"

    def log_path(self, mode: Mode, name: str) -> Path:
        if mode == "local":
            return self.instance_dir(mode, name) / "logs" / "odoo.log"
        return self.server_log_dir / f"{name}.log"

    def data_dir(self, mode: Mode, name: str) -> Path:
        if mode == "local":
            return self.instance_dir(mode, name) / "data"
        return self.server_data_dir / name

    def registry_path(self, mode: Mode) -> Path:
        return (self.state_dir if mode == "local" else self.server_etc) / "instances.json"

    def python_install_dir(self, mode: Mode) -> Path | None:
        # Server instances run as their own system user, so uv-managed Pythons must live
        # somewhere readable by everyone, not in root's home directory.
        return None if mode == "local" else self.server_root / "python"
