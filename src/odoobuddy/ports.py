"""Choosing free ports so several instances can run side by side."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .models import Instance

BASE_HTTP_PORT = 8069
GEVENT_OFFSET = 3  # Odoo's convention: 8069 for web, 8072 for websockets
STEP = 100  # 8069/8072, 8169/8172, 8269/8272, ...


def used_ports(instances: Iterable[Instance]) -> set[int]:
    return {p for i in instances for p in (i.http_port, i.gevent_port)}


def suggest_ports(instances: Iterable[Instance], in_use: Callable[[int], bool]) -> tuple[int, int]:
    """Return the first free (http, gevent) pair following Odoo's 8069/8072 pattern."""
    taken = used_ports(instances)
    for k in range(200):
        http = BASE_HTTP_PORT + k * STEP
        gevent = http + GEVENT_OFFSET
        if http > 65000:
            break
        if not ({http, gevent} & taken) and not in_use(http) and not in_use(gevent):
            return http, gevent
    raise RuntimeError("no free port pair found")


def gevent_port_for(http_port: int, instances: Iterable[Instance], in_use: Callable[[int], bool]) -> int:
    """Pick a websocket port for a user-chosen http port."""
    taken = used_ports(instances) | {http_port}
    for port in range(http_port + GEVENT_OFFSET, http_port + GEVENT_OFFSET + 200):
        if port not in taken and not in_use(port):
            return port
    raise RuntimeError(f"no free websocket port near {http_port}")


def check_port(port: int, instances: Iterable[Instance], in_use: Callable[[int], bool]) -> str:
    """Return an error message, or '' if the port can be used."""
    if not 1024 <= port <= 65535:
        return "choose a port between 1024 and 65535"
    for inst in instances:
        if port in (inst.http_port, inst.gevent_port):
            return f"port {port} is already used by instance {inst.name!r}"
    if in_use(port):
        return f"port {port} is already in use by another program"
    return ""
