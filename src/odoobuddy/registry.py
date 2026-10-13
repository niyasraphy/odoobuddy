"""The list of instances on this machine, and choosing which one a command acts on."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from .layout import Layout
from .models import Instance, Mode

MARKER_FILE = ".odoobuddy"
# Earlier releases were called "odooinstaller"; keep recognising what they created.
LEGACY_NAME = "odooinstaller"
LEGACY_MARKERS = (".odooinstaller",)


def migrate_legacy_state(layout: Layout) -> bool:
    """Move a pre-rename instance list (~/.local/share/odooinstaller) to its new place, once."""
    legacy = layout.state_dir.parent / LEGACY_NAME
    if layout.state_dir.exists() or not legacy.is_dir():
        return False
    try:
        legacy.rename(layout.state_dir)
    except OSError:
        return False
    return True


class RegistryError(RuntimeError):
    pass


class Registry:
    """Instances are stored per mode: local ones in the user's data dir, server ones in /etc."""

    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        migrate_legacy_state(layout)

    def _load(self, mode: Mode) -> dict[str, Instance]:
        path = self.layout.registry_path(mode)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except PermissionError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"cannot read {path}: {exc}") from None
        return {name: Instance.from_dict(data) for name, data in raw.get("instances", {}).items()}

    def _save(self, mode: Mode, instances: dict[str, Instance]) -> None:
        path = self.layout.registry_path(mode)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "instances": {n: i.to_dict() for n, i in sorted(instances.items())}}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        # The server registry holds no secrets; keep it readable so `odoobuddy list`
        # works without sudo. Local registries stay private to the user.
        tmp.chmod(0o644 if mode == "server" else 0o600)
        os.replace(tmp, path)

    def all(self) -> dict[str, Instance]:
        return {**self._load("server"), **self._load("local")}

    def get(self, name: str) -> Instance | None:
        return self.all().get(name)

    def add(self, instance: Instance) -> None:
        if instance.name in self.all():
            raise RegistryError(f"an instance named {instance.name!r} already exists")
        instances = self._load(instance.mode)
        instances[instance.name] = instance
        self._save(instance.mode, instances)

    def remove(self, name: str) -> None:
        modes: tuple[Mode, ...] = ("local", "server")
        for mode in modes:
            instances = self._load(mode)
            if name in instances:
                del instances[name]
                self._save(mode, instances)
        if self.current() == name:
            self.set_current(None)

    # --- the "current" instance, chosen with `odoobuddy use NAME` ---

    def _state_path(self) -> Path:
        return self.layout.state_dir / "state.json"

    def current(self) -> str | None:
        try:
            data = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        value = data.get("current")
        return value if isinstance(value, str) else None

    def set_current(self, name: str | None) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"current": name}) + "\n", encoding="utf-8")


def instance_from_cwd(cwd: Path) -> str | None:
    """If we are inside an instance folder, return its name (read from the marker file)."""
    for folder in (cwd, *cwd.parents):
        for marker_name in (MARKER_FILE, *LEGACY_MARKERS):
            marker = folder / marker_name
            if marker.is_file():
                try:
                    return marker.read_text(encoding="utf-8").strip() or None
                except OSError:
                    return None
    return None


def resolve(
    registry: Registry,
    name: str | None,
    *,
    cwd: Path,
    choose: Callable[[list[Instance]], Instance] | None,
) -> Instance:
    """Pick the instance a command applies to: explicit name > folder > `use` > ask."""
    instances = registry.all()
    if not instances:
        raise RegistryError("no instances yet, create one with: odoobuddy install")
    for candidate, source in (
        (name, "argument"),
        (instance_from_cwd(cwd), "folder"),
        (registry.current(), "current"),
    ):
        if candidate is None:
            continue
        if candidate in instances:
            return instances[candidate]
        if source == "argument":
            known = ", ".join(sorted(instances))
            raise RegistryError(f"no instance named {candidate!r}. Known instances: {known}")
    if choose is None:
        raise RegistryError(
            "which instance? Pass a name, run inside its folder, or set one with: odoobuddy use NAME"
        )
    return choose(sorted(instances.values(), key=lambda i: i.name))
