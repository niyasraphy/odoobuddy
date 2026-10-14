"""The interactive questions. Every answer can also be given as a command-line flag."""

from __future__ import annotations

import re
import socket
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from .models import (
    BACKEND_OFFSET,
    IDE_NAMES,
    Edition,
    Ide,
    InstallSpec,
    Instance,
    Mode,
    RepoSpec,
    validate_name,
)
from .ports import check_port, gevent_port_for, suggest_ports
from .sources import AccessResult
from .versions import major

DOMAIN_RE = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class WizardAbort(Exception):
    """The user cancelled, or the request cannot work (e.g. server mode without root)."""


class Prompter(Protocol):
    def select(self, message: str, choices: list[tuple[str, str]], default: str | None = None) -> str: ...
    def text(self, message: str, default: str = "", validate: Callable[[str], str] | None = None) -> str: ...
    def password(self, message: str) -> str: ...
    def confirm(self, message: str, default: bool = True) -> bool: ...
    def say(self, message: str) -> None: ...


class QuestionaryPrompter:
    """Real terminal prompts (arrow-key menus, hidden password input)."""

    def __init__(self, say: Callable[[str], None]) -> None:
        self._say = say

    @staticmethod
    def _ask(question: Any) -> Any:
        answer = question.ask()
        if answer is None:  # Ctrl+C
            raise WizardAbort("cancelled")
        return answer

    def select(self, message: str, choices: list[tuple[str, str]], default: str | None = None) -> str:
        import questionary

        items = [questionary.Choice(title=label, value=value) for value, label in choices]
        default_item = next((c for c in items if c.value == default), None)
        return str(self._ask(questionary.select(message, choices=items, default=default_item)))

    def text(self, message: str, default: str = "", validate: Callable[[str], str] | None = None) -> str:
        import questionary

        def check(value: str) -> bool | str:
            if validate is None:
                return True
            error = validate(value)
            return error or True

        return str(self._ask(questionary.text(message, default=default, validate=check))).strip()

    def password(self, message: str) -> str:
        import questionary

        return str(self._ask(questionary.password(message)))

    def confirm(self, message: str, default: bool = True) -> bool:
        import questionary

        return bool(self._ask(questionary.confirm(message, default=default)))

    def say(self, message: str) -> None:
        self._say(message)


def resolve_domain(domain: str) -> set[str]:
    try:
        return {str(info[4][0]) for info in socket.getaddrinfo(domain, 80, proto=socket.IPPROTO_TCP)}
    except OSError:
        return set()


def public_ip(timeout: float = 5) -> str | None:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=timeout) as response:
            return response.read().decode().strip() or None
    except OSError:
        return None


@dataclass
class WizardContext:
    """Everything the wizard needs from the outside world (swapped out in tests)."""

    instances: dict[str, Instance]
    versions: Callable[[], list[str]]
    port_in_use: Callable[[int], bool]
    check_enterprise: Callable[[str], AccessResult]
    is_root: bool
    suggested_workers: int
    resolve_domain: Callable[[str], set[str]] = resolve_domain
    public_ip: Callable[[], str | None] = public_ip
    preset: dict[str, Any] = field(default_factory=dict)
    # (url, branch, token) -> can we clone it? Used to check custom repositories up front.
    check_repo: Callable[[str, str, str | None], AccessResult] = lambda url, branch, token: AccessResult(
        True, "not checked"
    )
    # IDE -> launcher command (None when not installed), used to label and pre-select choices.
    installed_ides: dict[str, str | None] = field(default_factory=dict)


def _validate_name(instances: dict[str, Instance]) -> Callable[[str], str]:
    def check(value: str) -> str:
        try:
            validate_name(value)
        except ValueError as exc:
            return str(exc)
        if value in instances:
            return f"an instance named {value!r} already exists"
        return ""

    return check


def _validate_int(low: int, high: int) -> Callable[[str], str]:
    def check(value: str) -> str:
        return "" if value.isdigit() and low <= int(value) <= high else f"enter a number {low}-{high}"

    return check


def run_wizard(p: Prompter, ctx: WizardContext) -> InstallSpec:
    pre = ctx.preset
    instances = ctx.instances

    # 1. Local or server
    mode: Mode = pre.get("mode") or cast(
        Mode,
        p.select(
            "Where are you installing Odoo?",
            [
                ("local", "Local - my computer, for development"),
                ("server", "Server - production, with service, Nginx and SSL"),
            ],
            default="local",
        ),
    )
    if mode == "server" and not ctx.is_root:
        raise WizardAbort("server mode changes system settings, run it with sudo: sudo odoobuddy install")

    # 2. Version
    version = pre.get("version")
    if version is None:
        versions = ctx.versions()
        version = p.select(
            "Which Odoo version?",
            [(v, f"Odoo {v}" + ("  (latest)" if i == 0 else "")) for i, v in enumerate(versions)],
            default=versions[0],
        )

    # 3. Edition (+ GitHub token for Enterprise)
    edition: Edition = pre.get("edition") or cast(
        Edition,
        p.select(
            "Which edition?",
            [
                ("community", "Community (free, open source)"),
                ("enterprise", "Enterprise (needs a GitHub account with access to odoo/enterprise)"),
            ],
            default="community",
        ),
    )
    token: str | None = None
    if edition == "enterprise":
        token = pre.get("github_token")
        while True:
            if not token:
                token = p.password("GitHub token with access to odoo/enterprise (input hidden):")
            result = ctx.check_enterprise(token)
            if result.ok:
                p.say(f"[green]✓ {result.message}[/green]")
                break
            p.say(f"[red]✗ {result.message}[/red]")
            if not p.confirm("Try another token? (No = continue with Community)", default=False):
                p.say(
                    "[yellow]→ Continuing with Community. You can add Enterprise later "
                    "by reinstalling with a valid token.[/yellow]"
                )
                edition, token = "community", None
                break
            token = None

    # 4. Name
    default_name = f"odoo{major(version)}"
    n = 2
    while default_name in instances:
        default_name, n = f"odoo{major(version)}-{n}", n + 1
    name = pre.get("name") or p.text(
        "Instance name (used for folders, service and database):",
        default=default_name,
        validate=_validate_name(instances),
    )
    error = _validate_name(instances)(name)
    if error:
        raise WizardAbort(f"instance name: {error}")

    # 5. Ports
    others = list(instances.values())
    http_default, _ = suggest_ports(others, ctx.port_in_use)

    def port_ok(value: str) -> str:
        if not value.isdigit():
            return "enter a port number"
        return check_port(int(value), others, ctx.port_in_use)

    http_port = pre.get("http_port") or int(
        p.text("Which port should Odoo run on?", default=str(http_default), validate=port_ok)
    )
    error = check_port(int(http_port), others, ctx.port_in_use)
    if error:
        raise WizardAbort(error)
    gevent_port = gevent_port_for(int(http_port), others, ctx.port_in_use)

    spec = InstallSpec(
        name=name,
        version=version,
        mode=mode,
        edition=edition,
        http_port=int(http_port),
        gevent_port=gevent_port,
        github_token=token,
    )

    spec.repos = pre["repos"] if "repos" in pre else _repo_questions(p, ctx, version)

    if mode == "local":
        installed = ctx.installed_ides

        def label(ide: str) -> str:
            hint = "ready run configuration" if ide == "pycharm" else "debug with F5"
            text = f"{IDE_NAMES[ide]} ({hint})"
            if ide not in installed:
                return text
            return f"{text} - {'✓ installed' if installed[ide] else 'not found on this computer'}"

        # Installed editors first, so the likely answer is at the top.
        order = sorted(IDE_NAMES, key=lambda i: not installed.get(i))
        default_ide = next((i for i in order if installed.get(i)), "vscode")
        spec.ide = pre.get("ide") or cast(
            Ide,
            p.select(
                "Which IDE do you use? (it is not installed or changed, only configured)",
                [(i, label(i)) for i in order] + [("none", "None / other (I'll use ./start.sh)")],
                default=default_ide,
            ),
        )
        return spec

    _server_questions(p, ctx, spec)
    return spec


def _server_questions(p: Prompter, ctx: WizardContext, spec: InstallSpec) -> None:
    pre = ctx.preset
    has_domain = (
        bool(pre.get("domain"))
        if "domain" in pre
        else p.confirm("Do you have a domain name for this Odoo (e.g. erp.example.com)?", default=True)
    )
    if has_domain:
        domain = pre.get("domain") or p.text(
            "Domain name:",
            validate=lambda v: (
                "" if DOMAIN_RE.fullmatch(v.lower()) else "enter a domain like erp.example.com"
            ),
        )
        domain = domain.lower()
        if not DOMAIN_RE.fullmatch(domain):
            raise WizardAbort(f"invalid domain: {domain}")
        spec.domain = domain
        dns_ok = _check_dns(p, ctx, domain)
        want_ssl = (
            pre["ssl"]
            if "ssl" in pre
            else p.confirm("Install a free SSL certificate (HTTPS, Let's Encrypt)?", default=dns_ok)
        )
        if want_ssl:
            spec.ssl = True
            spec.email = (
                pre["email"]
                if "email" in pre
                else p.text(
                    "Email for SSL expiry notices (optional, Enter to skip):",
                    validate=lambda v: "" if not v or EMAIL_RE.fullmatch(v) else "enter a valid email",
                )
                or None
            )
    else:
        p.say(f"[dim]Without a domain, Odoo will be reachable at http://SERVER-IP:{spec.http_port}[/dim]")

    if not spec.domain:
        # Nginx takes the public port; Odoo listens internally on the offset ports.
        for port in (spec.http_port + BACKEND_OFFSET, spec.gevent_port + BACKEND_OFFSET):
            if port > 65535 or ctx.port_in_use(port):
                raise WizardAbort(
                    f"Odoo needs internal port {port} (your port + {BACKEND_OFFSET}), which is not free; "
                    "choose another port"
                )
    spec.workers = pre.get("workers") or int(
        p.text(
            "Number of Odoo workers (based on this server's CPU and RAM):",
            default=str(ctx.suggested_workers),
            validate=_validate_int(1, 64),
        )
    )
    spec.firewall = (
        pre["firewall"]
        if "firewall" in pre
        else p.confirm("Enable the firewall (only SSH and web traffic allowed)?", default=True)
    )


def _check_dns(p: Prompter, ctx: WizardContext, domain: str) -> bool:
    """Let's Encrypt fails if the domain does not point here, so check before trying."""
    addresses = ctx.resolve_domain(domain)
    if not addresses:
        p.say(
            f"[yellow]⚠ {domain} does not resolve yet. Add a DNS 'A' record pointing to this server.[/yellow]"
        )
        return False
    ip = ctx.public_ip()
    if ip and ip not in addresses:
        p.say(
            f"[yellow]⚠ {domain} points to {', '.join(sorted(addresses))}, "
            f"but this server's public IP is {ip}. SSL will fail until DNS is updated.[/yellow]"
        )
        return False
    p.say(f"[green]✓ {domain} points to this server[/green]")
    return True


def _repo_questions(p: Prompter, ctx: WizardContext, version: str) -> list[RepoSpec]:
    """Optionally collect custom module repositories to clone into custom/."""
    repos: list[RepoSpec] = []
    if not p.confirm("Clone custom module repositories now? (you can also add them later)", default=False):
        return repos
    p.say("[dim]Enter one repository at a time; leave the URL empty to finish.[/dim]")
    while True:
        url = p.text("Repository URL (https://... or git@...):")
        if not url:
            return repos
        branch = p.text("Branch:", default=version)
        token: str | None = None
        result = ctx.check_repo(url, branch, None)
        if not result.ok and result.needs_auth:
            p.say(f"[yellow]{result.message}[/yellow]")
            token = p.password("Access token for this repository (Enter to skip it):") or None
            if token:
                result = ctx.check_repo(url, branch, token)
        if result.ok:
            p.say(f"[green]✓ {url} ({branch}) will be cloned[/green]")
            repos.append(RepoSpec(url=url, branch=branch, token=token))
        else:
            p.say(f"[red]✗ skipped {url}: {result.message}[/red]")


# Settings that can be changed from the summary, and the answers each one invalidates.
_SERVER_KEYS = ("domain", "ssl", "email", "workers", "firewall")
EDITABLE: dict[str, tuple[str, tuple[str, ...]]] = {
    "mode": ("Local or server", ("mode", "ide", *_SERVER_KEYS)),
    "version": ("Odoo version", ("version",)),
    "edition": ("Edition (Community / Enterprise)", ("edition", "github_token")),
    "name": ("Instance name", ("name",)),
    "http_port": ("Port", ("http_port",)),
    "ide": ("IDE", ("ide",)),
    "repos": ("Custom repositories", ("repos",)),
    "domain": ("Domain name", ("domain", "ssl", "email")),
    "ssl": ("SSL certificate", ("ssl", "email")),
    "workers": ("Number of workers", ("workers",)),
    "firewall": ("Firewall", ("firewall",)),
}


def editable_fields(spec: InstallSpec) -> list[tuple[str, str]]:
    """(key, label with current value) for the settings that apply to this spec."""
    current = {
        "mode": spec.mode,
        "version": spec.version,
        "edition": spec.edition,
        "name": spec.name,
        "http_port": str(spec.http_port),
        "ide": spec.ide,
        "repos": f"{len(spec.repos)} selected",
        "domain": spec.domain or "none",
        "ssl": "yes" if spec.ssl else "no",
        "workers": str(spec.workers),
        "firewall": "yes" if spec.firewall else "no",
    }
    keys = ["mode", "version", "edition", "name", "http_port", "repos"]
    keys += (
        ["ide"]
        if spec.mode == "local"
        else ["domain", *(["ssl"] if spec.domain else []), "workers", "firewall"]
    )
    return [(k, f"{EDITABLE[k][0]}: {current[k]}") for k in keys]


def answers_without(spec: InstallSpec, key: str) -> dict[str, Any]:
    """The spec's answers as presets, minus the ones that must be asked again to change ``key``."""
    preset: dict[str, Any] = {
        "mode": spec.mode,
        "version": spec.version,
        "edition": spec.edition,
        "name": spec.name,
        "http_port": spec.http_port,
        "repos": list(spec.repos),
    }
    if spec.github_token:
        preset["github_token"] = spec.github_token
    if spec.mode == "local":
        preset["ide"] = spec.ide
    else:
        preset.update(domain=spec.domain, workers=spec.workers, firewall=spec.firewall)
        if spec.domain:
            preset.update(ssl=spec.ssl, email=spec.email)
    for dropped in EDITABLE[key][1]:
        preset.pop(dropped, None)
    return preset
