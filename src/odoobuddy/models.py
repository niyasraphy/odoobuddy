"""Data types shared across the package."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

Mode = Literal["local", "server"]
Edition = Literal["community", "enterprise"]
Ide = Literal["vscode", "pycharm", "none"]

NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,30}")


def validate_name(name: str) -> str:
    """Instance names become folder, service, user and database names, so keep them simple."""
    if not NAME_RE.fullmatch(name) or name.endswith("-"):
        raise ValueError("use 1-31 lowercase letters, digits or '-', starting with a letter (e.g. 'shop-18')")
    return name


def db_role_for(name: str) -> str:
    return "odoo_" + name.replace("-", "_")


def system_user_for(name: str) -> str:
    return f"odoo-{name}"


def service_name_for(name: str) -> str:
    return f"odoo-{name}"


@dataclass
class RepoSpec:
    """A custom-module repository to clone into the instance's custom/ folder."""

    url: str
    branch: str
    token: str | None = field(default=None, repr=False)


@dataclass
class InstallSpec:
    """Everything the user chose in the wizard (or passed as flags)."""

    name: str
    version: str
    mode: Mode
    edition: Edition = "community"
    http_port: int = 8069
    gevent_port: int = 8072
    ide: Ide = "none"
    domain: str | None = None
    ssl: bool = False
    email: str | None = None
    workers: int = 0
    firewall: bool = False
    repos: list[RepoSpec] = field(default_factory=list)
    github_token: str | None = field(default=None, repr=False)


@dataclass
class Instance:
    """A created Odoo instance, as stored in the registry."""

    name: str
    version: str
    edition: Edition
    mode: Mode
    http_port: int
    gevent_port: int
    db_user: str
    python: str
    instance_dir: Path
    config_path: Path
    log_path: Path
    data_dir: Path
    venv_dir: Path
    community_dir: Path
    enterprise_dir: Path | None = None
    domain: str | None = None
    ssl: bool = False
    ide: Ide = "none"
    created_at: str = ""

    @property
    def odoo_bin(self) -> Path:
        return self.community_dir / "odoo-bin"

    @property
    def custom_dir(self) -> Path:
        return self.instance_dir / "custom"

    @property
    def python_bin(self) -> Path:
        return self.venv_dir / "bin" / "python"

    @property
    def pid_file(self) -> Path:
        return self.instance_dir / "odoo.pid"

    @property
    def service(self) -> str:
        return service_name_for(self.name)

    @property
    def system_user(self) -> str:
        return system_user_for(self.name)

    @property
    def url(self) -> str:
        if self.domain:
            return f"{'https' if self.ssl else 'http'}://{self.domain}"
        return f"http://localhost:{self.http_port}"

    @property
    def label(self) -> str:
        return f"{self.name} · Odoo {self.version} {self.edition.title()}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: str(v) if isinstance(v, Path) else v for k, v in data.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Instance:
        paths = {
            "instance_dir",
            "config_path",
            "log_path",
            "data_dir",
            "venv_dir",
            "community_dir",
            "enterprise_dir",
        }
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        for key in paths:
            if known.get(key) is not None:
                known[key] = Path(known[key])
        return cls(**known)
