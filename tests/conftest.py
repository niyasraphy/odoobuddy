from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from odooinstaller.layout import Layout
from odooinstaller.models import Instance, db_role_for
from odooinstaller.registry import Registry
from odooinstaller.system import Runner


class RecordingRunner(Runner):
    """Records commands instead of running them; file writes still happen (into tmp_path)."""

    def __init__(
        self,
        *,
        root: bool = False,
        on_run: Callable[[list[str]], None] | None = None,
        fail_on: str | None = None,
        returncodes: dict[str, int] | None = None,
    ) -> None:
        super().__init__(console=Console(quiet=True), dry_run=False, log_file=None, root=root)
        self.commands: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.envs: list[dict[str, str] | None] = []
        self.on_run = on_run
        self.fail_on = fail_on
        self.returncodes = returncodes or {"id -u": 1}  # by default, system users don't exist yet

    def run(self, cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        full = self._prefix(sudo=kwargs.get("sudo", False), as_user=kwargs.get("as_user")) + [
            str(c) for c in cmd
        ]
        self.commands.append(full)
        self.inputs.append(kwargs.get("input"))
        env = kwargs.get("env")
        self.envs.append(dict(env) if env else None)
        if self.on_run:
            self.on_run(full)
        if self.fail_on and " ".join(full).startswith(self.fail_on):
            from odooinstaller.system import CommandError

            raise CommandError(full, 1, "simulated failure")
        line = " ".join(full)
        code = next((rc for prefix, rc in self.returncodes.items() if line.startswith(prefix)), 0)
        return subprocess.CompletedProcess(full, code, "", "")

    def mkdir(self, path: Path, *, owner: str | None = None, mode: int = 0o755) -> None:
        super().mkdir(path, owner=None, mode=mode)

    def write_file(self, path: Path, content: str, *, mode: int = 0o644, owner: str | None = None) -> None:
        super().write_file(path, content, mode=mode, owner=None)

    def joined(self) -> list[str]:
        return [" ".join(c) for c in self.commands]


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    return Layout.under(tmp_path)


@pytest.fixture
def registry(layout: Layout) -> Registry:
    return Registry(layout)


@pytest.fixture
def make_instance(layout: Layout) -> Callable[..., Instance]:
    def factory(name: str = "shop", version: str = "18.0", mode: str = "local", **overrides: Any) -> Instance:
        instance_dir = layout.instance_dir(mode, name)  # type: ignore[arg-type]
        data = dict(
            name=name,
            version=version,
            edition="community",
            mode=mode,
            http_port=8069,
            gevent_port=8072,
            db_name=name,
            db_user=db_role_for(name),
            python="3.12",
            instance_dir=instance_dir,
            config_path=layout.config_path(mode, name),  # type: ignore[arg-type]
            log_path=layout.log_path(mode, name),  # type: ignore[arg-type]
            data_dir=layout.data_dir(mode, name),  # type: ignore[arg-type]
            venv_dir=instance_dir / "venv",
            community_dir=layout.shared_source(mode, version, "community"),  # type: ignore[arg-type]
        )
        data.update(overrides)
        return Instance(**data)  # type: ignore[arg-type]

    return factory
