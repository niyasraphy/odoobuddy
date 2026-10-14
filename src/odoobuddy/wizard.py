"""The interactive questions. Every answer can also be given as a command-line flag."""

from __future__ import annotations

import json
import re
import socket
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from .ide import EditorInstall, install_hint
from .models import (
    BACKEND_OFFSET,
    DEBUG_OFFSET,
    IDE_NAMES,
    PURPOSES,
    Edition,
    Ide,
    InstallSpec,
    Instance,
    Mode,
    Purpose,
    RepoSpec,
    RunMode,
    default_purpose,
    mode_for,
    validate_name,
)
from .ports import check_port, gevent_port_for, suggest_ports
from .sources import AccessResult
from .system import public_ip
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

        # auto_enter=False: typing y or n only selects; Enter confirms, so a stray key can be corrected.
        return bool(self._ask(questionary.confirm(message, default=default, auto_enter=False)))

    def say(self, message: str) -> None:
        # Indented, so hints and results read as part of the question above them.
        self._say("\n".join("  " + line for line in message.split("\n")))


def resolve_domain(domain: str) -> set[str]:
    """IPv4 addresses of ``domain``.

    Asks a public resolver first: the local one may cache "not found" for a while, so a
    record the user just added would not show up when they check again.
    """
    request = urllib.request.Request(
        f"https://cloudflare-dns.com/dns-query?name={urllib.parse.quote(domain)}&type=A",
        headers={"Accept": "application/dns-json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            answers = json.loads(response.read()).get("Answer") or []
        return {str(a["data"]) for a in answers if a.get("type") == 1}
    except (OSError, ValueError, KeyError):
        pass
    try:
        infos = socket.getaddrinfo(domain, 80, family=socket.AF_INET, proto=socket.IPPROTO_TCP)
        return {str(info[4][0]) for info in infos}
    except OSError:
        return set()


@dataclass
class WizardContext:
    """Everything the wizard needs from the outside world (swapped out in tests)."""

    instances: dict[str, Instance]
    versions: Callable[[], list[str]]
    port_in_use: Callable[[int], bool]
    check_enterprise: Callable[[str], AccessResult]
    is_root: bool
    suggested_workers: int
    # Most workers this machine can take next to the instances already on it.
    max_workers: int = 64
    # Where the wizard thinks it runs, and why: the default answer to "local or server".
    detected_mode: Mode = "local"
    detected_reason: str = ""
    # Free RAM (MB) and disk (GB) for the production check; 0 = unknown, not checked.
    available_mb: int = 0
    free_disk_gb: float = 0.0
    # False when installing from a script (--yes): never stop to ask, only report.
    interactive: bool = True
    resolve_domain: Callable[[str], set[str]] = resolve_domain
    public_ip: Callable[[], str | None] = public_ip
    preset: dict[str, Any] = field(default_factory=dict)
    # (url, branch, token) -> can we clone it? Used to check custom repositories up front.
    check_repo: Callable[[str, str, str | None], AccessResult] = lambda url, branch, token: AccessResult(
        True, "not checked"
    )
    # IDE -> launcher command (None when not installed), used to label and pre-select choices.
    installed_ides: dict[str, str | None] = field(default_factory=dict)
    # IDE -> launcher command, looked up again after the user installs an editor.
    find_ide: Callable[[str], str | None] = lambda ide: None
    # IDE -> how odoobuddy can install it here, or None (then the user installs it).
    editor_install: Callable[[str], EditorInstall | None] = lambda ide: None


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


TEST_WORKERS = 2  # default for test instances, which should leave the server to production
WORKER_MB = 300  # rough RAM of one Odoo worker (same estimate as the installer's)
MIN_FREE_DISK_GB = 5


def _too_many(most: int) -> str:
    return (
        f"at most {most}. Each worker needs about 300 MB of RAM and a share of the CPU; "
        "too many make Odoo slow or get it stopped when memory runs out"
    )


def _validate_workers(most: int) -> Callable[[str], str]:
    def check(value: str) -> str:
        if not value.isdigit() or int(value) < 1:
            return "enter a number of workers, e.g. 2"
        if int(value) > most:
            return f"⚠ Too many: this server can run at most {most} (about 300 MB of RAM each)"
        return ""

    return check


def run_wizard(p: Prompter, ctx: WizardContext) -> InstallSpec:
    pre = ctx.preset
    instances = ctx.instances

    # 1. What is it for? (decides local or server, and which questions follow)
    purpose = _purpose_question(p, ctx)
    mode = mode_for(purpose)
    if mode == "server" and not ctx.is_root:
        raise WizardAbort(
            "a server install changes system settings, run it with sudo: sudo odoobuddy install"
        )
    # A test instance follows the production one on this server: same version and edition.
    production = next((i for i in instances.values() if i.role == "production"), None)
    follow = production if purpose == "test" else None

    # 2. Version
    version = pre.get("version")
    if version is None:
        versions = ctx.versions()
        default_version = follow.version if follow and follow.version in versions else versions[0]
        version = p.select(
            "Which Odoo version?",
            [
                (
                    v,
                    f"Odoo {v}"
                    + ("  (latest)" if i == 0 else "")
                    + ("  (production)" if follow and v == follow.version else ""),
                )
                for i, v in enumerate(versions)
            ],
            default=default_version,
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
            default=follow.edition if follow else "community",
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
    base = f"odoo{major(version)}"
    if purpose == "test":
        base = f"{follow.name}-test" if follow else f"{base}-test"
    default_name, n = base, 2
    while default_name in instances:
        default_name, n = f"{base}-{n}", n + 1
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

    if pre.get("http_port"):
        http_port = pre["http_port"]
    elif _asks(ctx, purpose, "http_port"):
        if http_default != 8069:
            p.say(
                f"[dim]Port 8069 and up to {http_default - 1} are used by other instances or programs.[/dim]"
            )
        http_port = int(p.text("Which port should Odoo run on?", default=str(http_default), validate=port_ok))
    else:
        http_port = http_default  # test and development: the next free port (changeable in the summary)
    error = check_port(int(http_port), others, ctx.port_in_use)
    if error:
        raise WizardAbort(error)
    gevent_port = gevent_port_for(int(http_port), others, ctx.port_in_use)

    spec = InstallSpec(
        name=name,
        version=version,
        mode=mode,
        edition=edition,
        purpose=purpose,
        http_port=int(http_port),
        gevent_port=gevent_port,
        github_token=token,
    )

    # Custom repositories: asked for production; test and development add them from the summary.
    if "repos" in pre:
        spec.repos = pre["repos"]
    elif _asks(ctx, purpose, "repos"):
        spec.repos = _repo_questions(p, ctx, version)

    if mode == "local":
        _run_question(p, ctx, spec)
        return spec

    _server_questions(p, ctx, spec)
    if purpose == "production":
        _production_check(p, ctx, spec)
    return spec


def _asks(ctx: WizardContext, purpose: Purpose, key: str) -> bool:
    """Production asks every question; test and development only the setting being changed."""
    return purpose == "production" or ctx.preset.get(EDITING) == key


def _purpose_question(p: Prompter, ctx: WizardContext) -> Purpose:
    pre = ctx.preset
    if pre.get("purpose"):
        return cast(Purpose, pre["purpose"])
    if pre.get("mode"):  # --mode from older scripts: server meant production
        return default_purpose(pre["mode"])
    detected = default_purpose(ctx.detected_mode)
    mark = f"  ← detected: {ctx.detected_reason}" if ctx.detected_reason else ""
    choices = [
        ("production", "Production - real company data, on this server"),
        ("test", "Test / staging - try updates and modules before production, on this server"),
        ("development", "Development - write and debug modules, on this computer"),
    ]
    return cast(
        Purpose,
        p.select(
            "What is this Odoo for?",
            [(value, label + (mark if value == detected else "")) for value, label in choices],
            default=detected,
        ),
    )


def _server_questions(p: Prompter, ctx: WizardContext, spec: InstallSpec) -> None:
    pre = ctx.preset
    production = spec.role == "production"
    dns_ok = False
    if "domain" in pre:
        domain = pre["domain"] or ""
    else:
        example = "erp.example.com" if production else "test.erp.example.com"
        domain = p.text(
            f"Domain name (e.g. {example}), or Enter to use the server's IP address:",
            validate=lambda v: (
                "" if not v or DOMAIN_RE.fullmatch(v.lower()) else f"enter a domain like {example}"
            ),
        )
    if domain:
        domain = domain.lower()
        if not DOMAIN_RE.fullmatch(domain):
            raise WizardAbort(f"invalid domain: {domain}")
        spec.domain = domain
        dns_ok = _confirm_dns(p, ctx, spec, ask="domain" not in pre)
    if spec.domain:
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
        ip = ctx.public_ip() or "SERVER-IP"
        p.say(f"[dim]Without a domain, Odoo will be reachable at http://{ip}:{spec.http_port}[/dim]")

    if not spec.domain:
        # Nginx takes the public port; Odoo listens internally on the offset ports.
        for port in (spec.http_port + BACKEND_OFFSET, spec.gevent_port + BACKEND_OFFSET):
            if port > 65535 or ctx.port_in_use(port):
                raise WizardAbort(
                    f"Odoo needs internal port {port} (your port + {BACKEND_OFFSET}), which is not free; "
                    "choose another port"
                )
    most = ctx.max_workers
    if pre.get("workers") and pre["workers"] > most:
        raise WizardAbort(f"--workers {pre['workers']} is more than this server can run: {_too_many(most)}")
    if pre.get("workers"):
        spec.workers = pre["workers"]
    else:
        # A test instance stays small, so production keeps most of the server.
        default = min(ctx.suggested_workers if production else TEST_WORKERS, most)
        p.say("[dim]Workers handle requests in parallel, about 300 MB of RAM each.[/dim]")
        spec.workers = int(
            p.text(
                f"Number of Odoo workers (at most {most} for this server's CPU, RAM and other instances):",
                default=str(default),
                validate=_validate_workers(most),
            )
        )
    if "firewall" in pre:
        spec.firewall = pre["firewall"]
    elif _asks(ctx, spec.role, "firewall"):
        spec.firewall = p.confirm("Enable the firewall (only SSH and web traffic allowed)?", default=True)
    if "daily_backups" in pre:
        spec.daily_backups = pre["daily_backups"]
    elif _asks(ctx, spec.role, "daily_backups"):
        spec.daily_backups = p.confirm(
            "Back up every database each night (the last 7 are kept)?", default=True
        )


def _production_check(p: Prompter, ctx: WizardContext, spec: InstallSpec) -> None:
    """Point out risky production choices, and offer to apply the recommended ones."""
    issues: list[tuple[str, str, Callable[[], None] | None]] = []  # (problem, advice, fix)
    if not spec.domain:
        issues.append(
            (
                "No domain: Odoo uses plain HTTP, so passwords travel unencrypted",
                "add a domain with HTTPS: choose 'Change a setting' → Domain name in the next menu",
                None,
            )
        )
    elif not spec.ssl:
        issues.append(
            (
                f"{spec.domain} without HTTPS: passwords travel unencrypted",
                "turn on the free Let's Encrypt certificate",
                lambda: setattr(spec, "ssl", True),
            )
        )
    if not spec.daily_backups:
        issues.append(
            (
                "No nightly backups: a broken disk or mistake can lose your data",
                "turn on nightly backups",
                lambda: setattr(spec, "daily_backups", True),
            )
        )
    if not spec.firewall:
        issues.append(
            (
                "Firewall off: every open port on this server is reachable from the internet",
                "turn on the firewall (SSH and web stay open)",
                lambda: setattr(spec, "firewall", True),
            )
        )
    if ctx.available_mb:
        fit = max(1, ctx.available_mb // WORKER_MB - 1)  # - 1: the cron process
        if spec.workers > fit:
            issues.append(
                (
                    f"{ctx.available_mb / 1024:.1f} GB of RAM free for {spec.workers} workers: "
                    "Odoo may be stopped when memory runs out",
                    f"use {fit} worker{'s' if fit > 1 else ''}",
                    lambda: setattr(spec, "workers", fit),
                )
            )
    if ctx.free_disk_gb and ctx.free_disk_gb < MIN_FREE_DISK_GB:
        issues.append(
            (
                f"Only {ctx.free_disk_gb:.1f} GB of free disk space: a full disk can corrupt data",
                "free up space or add a bigger disk before going live",
                None,
            )
        )
    if not issues:
        p.say("[green]✓ Production check: HTTPS, nightly backups, firewall and workers look good[/green]")
        return
    p.say("[bold]Production check[/bold]")
    for problem, advice, _ in issues:
        p.say(f"[yellow]⚠ {problem}[/yellow]\n  [dim]Recommended: {advice}[/dim]")
    fixes = [(advice, fix) for _, advice, fix in issues if fix is not None]
    if not fixes or not ctx.interactive:
        return
    choice = p.select(
        "What do you want to do?",
        [("apply", "Apply the recommendations"), ("keep", "Keep my choices (at my own risk)")],
        default="apply",
    )
    if choice == "apply":
        for advice, fix in fixes:
            fix()
            p.say(f"[green]✓ {advice[0].upper() + advice[1:]}[/green]")


def _dns_problem(ctx: WizardContext, domain: str, ip: str | None) -> str | None:
    """Why Let's Encrypt would fail for ``domain``, or None if it points to this server."""
    addresses = ctx.resolve_domain(domain)
    if not addresses:
        return f"{domain} has no DNS 'A' record yet"
    if ip and ip not in addresses:
        return f"{domain} points to {', '.join(sorted(addresses))}, not to this server ({ip})"
    return None


def _dns_record_help(domain: str, ip: str | None) -> str:
    labels = domain.split(".")
    host = labels[0] if len(labels) > 2 else "@"
    target = ip or "this server's public IP"
    return (
        "  Add this record where you manage your domain (registrar or DNS provider):\n"
        f"    Type: [bold]A[/bold]    Name/Host: [bold]{host}[/bold]    "
        f"Value/Points to: [bold]{target}[/bold]\n"
        "  [dim]Name is the part before your main domain (@ means the domain itself). "
        "Changes usually show up within a few minutes.[/dim]"
    )


def _confirm_dns(p: Prompter, ctx: WizardContext, spec: InstallSpec, *, ask: bool) -> bool:
    """Check the domain points here; if not, help the user fix it or go on without it.

    Returns whether DNS is ready. May clear ``spec.domain`` when the user drops the domain.
    """
    assert spec.domain
    domain = spec.domain
    ip = ctx.public_ip()
    while True:
        problem = _dns_problem(ctx, domain, ip)
        if problem is None:
            p.say(f"[green]✓ {domain} points to this server[/green]")
            return True
        p.say(f"[yellow]⚠ {problem}. The SSL certificate needs it.[/yellow]")
        if not ask:
            return False
        p.say(_dns_record_help(domain, ip))
        choice = p.select(
            "What do you want to do?",
            [
                ("check", "I have added the record - check again"),
                ("none", f"Continue without a domain (http://{ip or 'SERVER-IP'}:{spec.http_port})"),
                ("keep", f"Keep {domain} anyway and add SSL later"),
            ],
            default="check",
        )
        if choice == "none":
            spec.domain = None
            return False
        if choice == "keep":
            return False


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


# Preset key naming the setting being changed from the summary, so it is asked even when its
# purpose normally uses the default (e.g. the port of a development instance).
EDITING = "_editing"

# Settings that can be changed from the summary, and the answers each one invalidates.
_SERVER_KEYS = ("domain", "ssl", "email", "workers", "firewall", "daily_backups")
EDITABLE: dict[str, tuple[str, tuple[str, ...]]] = {
    "purpose": ("Purpose", ("purpose", "mode", "ide", "run_mode", "install_editor", *_SERVER_KEYS)),
    "version": ("Odoo version", ("version",)),
    "edition": ("Edition (Community / Enterprise)", ("edition", "github_token")),
    "name": ("Instance name", ("name",)),
    "http_port": ("Port", ("http_port",)),
    "ide": ("How Odoo runs", ("ide", "run_mode", "install_editor")),
    "repos": ("Custom repositories", ("repos",)),
    "domain": ("Domain name", ("domain", "ssl", "email")),
    "ssl": ("SSL certificate", ("ssl", "email")),
    "workers": ("Number of workers", ("workers",)),
    "firewall": ("Firewall", ("firewall",)),
    "daily_backups": ("Nightly backups", ("daily_backups",)),
}


def editable_fields(spec: InstallSpec) -> list[tuple[str, str]]:
    """(key, label with current value) for the settings that apply to this spec."""
    current = {
        "purpose": PURPOSES[spec.role],
        "version": spec.version,
        "edition": spec.edition,
        "name": spec.name,
        "http_port": str(spec.http_port),
        "ide": IDE_NAMES.get(spec.ide, spec.run_mode),
        "repos": f"{len(spec.repos)} selected",
        "domain": spec.domain or "none",
        "ssl": "yes" if spec.ssl else "no",
        "workers": str(spec.workers),
        "firewall": "yes" if spec.firewall else "no",
        "daily_backups": "yes" if spec.daily_backups else "no",
    }
    keys = ["purpose", "version", "edition", "name", "http_port", "repos"]
    keys += (
        ["ide"]
        if spec.mode == "local"
        else ["domain", *(["ssl"] if spec.domain else []), "workers", "firewall", "daily_backups"]
    )
    return [(k, f"{EDITABLE[k][0]}: {current[k]}") for k in keys]


def answers_without(spec: InstallSpec, key: str) -> dict[str, Any]:
    """The spec's answers as presets, minus the ones that must be asked again to change ``key``."""
    preset: dict[str, Any] = {
        "purpose": spec.role,
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
        preset["run_mode"] = spec.run_mode
        preset["install_editor"] = spec.install_editor
    else:
        preset.update(
            domain=spec.domain, workers=spec.workers, firewall=spec.firewall, daily_backups=spec.daily_backups
        )
        if spec.domain:
            preset.update(ssl=spec.ssl, email=spec.email)
    for dropped in EDITABLE[key][1]:
        preset.pop(dropped, None)
    preset[EDITING] = key
    return preset


def _run_question(p: Prompter, ctx: WizardContext, spec: InstallSpec) -> None:
    """Local only: open in an editor (Odoo runs inside it), or run in a terminal / background."""
    pre = ctx.preset
    if "ide" in pre or "run_mode" in pre:
        spec.ide = pre.get("ide", "none")
        spec.run_mode = pre.get("run_mode") or ("editor" if spec.ide != "none" else "background")
        spec.install_editor = bool(pre.get("install_editor")) and spec.ide != "none"
        _check_debug_port(ctx, spec)
        return
    installed = ctx.installed_ides

    def label(ide: str) -> str:
        text = f"In {IDE_NAMES[ide]} (opens automatically, Odoo runs inside it)"
        if ide not in installed:
            return text
        return f"{text} - {'✓ installed' if installed[ide] else 'not found on this computer'}"

    # Installed editors first, so the likely answer is at the top.
    order = sorted(IDE_NAMES, key=lambda i: not installed.get(i))
    default = next((i for i in order if installed.get(i)), "terminal")
    spec.install_editor = False
    while True:
        choice = p.select(
            "How do you want to run Odoo? (only the project is configured, editor settings stay as they are)",
            [(i, label(i)) for i in order]
            + [
                ("terminal", f"In a terminal, no editor (odoobuddy run {spec.name})"),
                ("background", f"In the background (odoobuddy start / stop / logs {spec.name})"),
            ],
            default=default,
        )
        missing = choice in installed and not installed[choice]
        if not missing or _use_missing_editor(p, ctx, spec, choice):
            break
    if choice in IDE_NAMES:
        spec.ide, spec.run_mode = cast(Ide, choice), "editor"
    else:
        spec.ide, spec.run_mode = "none", cast(RunMode, choice)
    _check_debug_port(ctx, spec)


def _use_missing_editor(p: Prompter, ctx: WizardContext, spec: InstallSpec, ide: str) -> bool:
    """The chosen editor is not installed: True to go on with it, False to choose again.

    Offers to install it during the Odoo install when odoobuddy can, otherwise asks the
    user to install it now and checks again before continuing.
    """
    name = IDE_NAMES[ide]
    auto = ctx.editor_install(ide)
    p.say(f"[yellow]⚠ {name} is not installed on this computer.[/yellow]")
    if auto is None:
        p.say(
            f"  Please {install_hint(ide)}\n"
            '  Then come back here and choose "check again" - this window waits for you.'
        )
    choices = [("install", f"Install {name} now ({auto.how})")] if auto else []
    choices += [
        ("check", f"I have installed {name} - check again"),
        ("later", f"Use {name} anyway, I will install it later"),
        ("back", "Choose another way to run Odoo"),
    ]
    while True:
        action = p.select("What do you want to do?", choices, default=choices[0][0])
        if action == "install":
            spec.install_editor = True
            p.say(f"[dim]{name} will be installed together with Odoo, then opened.[/dim]")
            return True
        if action == "back":
            return False
        if action == "later":
            return True
        found = ctx.find_ide(ide)
        if found:
            ctx.installed_ides[ide] = found
            p.say(f"[green]✓ {name} found ({found})[/green]")
            return True
        p.say(
            f"[yellow]{name} is still not found.[/yellow] Finish installing it "
            f"({install_hint(ide)}), then check again."
        )


def _check_debug_port(ctx: WizardContext, spec: InstallSpec) -> None:
    port = spec.http_port + DEBUG_OFFSET
    if port > 65535 or ctx.port_in_use(port):
        raise WizardAbort(
            f"the debugger needs port {port} (your port + {DEBUG_OFFSET}), which is not free; "
            "choose another port"
        )
