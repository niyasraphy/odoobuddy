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
        if root := os.environ.get("ODOOBUDDY_ROOT"):
            return cls.under(Path(root))
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return cls(
            local_home=Path(os.environ.get("ODOOBUDDY_HOME", Path.home() / "odoo")),
            state_dir=data_home / "odoobuddy",
            server_root=Path("/opt/odoo"),
            server_etc=Path("/etc/odoobuddy"),
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
            state_dir=root / "home" / ".local" / "share" / "odoobuddy",
            server_root=root / "opt" / "odoo",
            server_etc=root / "etc" / "odoobuddy",
            server_conf_dir=root / "etc" / "odoo",
            server_log_dir=root / "var" / "log" / "odoo",
            server_data_dir=root / "var" / "lib" / "odoo",
            systemd_dir=root / "etc" / "systemd" / "system",
            nginx_dir=root / "etc" / "nginx",
            logrotate_dir=root / "etc" / "logrotate.d",
        )

    def instance_dir(self, mode: Mode, name: str) -> Path:
        return (self.local_home if mode == "local" else self.server_root) / name

    # Inside an instance folder:  odoo/odoo (community), odoo/enterprise, custom/, conf/
    def community_dir(self, mode: Mode, name: str) -> Path:
        return self.instance_dir(mode, name) / "odoo" / "odoo"

    def enterprise_dir(self, mode: Mode, name: str) -> Path:
        return self.instance_dir(mode, name) / "odoo" / "enterprise"

    def custom_dir(self, mode: Mode, name: str) -> Path:
        return self.instance_dir(mode, name) / "custom"

    def config_path(self, mode: Mode, name: str) -> Path:
        return self.instance_dir(mode, name) / "conf" / "odoo.conf"

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
        # somewhere readable by everyone, not in root's home directory. Hidden, so it cannot
        # clash with an instance folder (names start with a letter).
        return None if mode == "local" else self.server_root / ".python"
