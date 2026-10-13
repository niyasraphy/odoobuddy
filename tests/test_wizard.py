from collections import deque

import pytest

from odooinstaller.sources import AccessResult
from odooinstaller.wizard import WizardAbort, WizardContext, run_wizard


class Scripted:
    """Answers questions in order, and records what was asked and said."""

    def __init__(self, *answers):
        self.answers = deque(answers)
        self.asked: list[str] = []
        self.said: list[str] = []

    def _next(self, message):
        self.asked.append(message)
        if not self.answers:
            raise AssertionError(f"unexpected question: {message}")
        return self.answers.popleft()

    def select(self, message, choices, default=None):
        answer = self._next(message)
        assert answer in [value for value, _ in choices], (answer, choices)
        return answer

    def text(self, message, default="", validate=None):
        answer = self._next(message)
        if answer == "":
            answer = default
        if validate:
            assert validate(answer) == "", validate(answer)
        return answer

    def password(self, message):
        return self._next(message)

    def confirm(self, message, default=True):
        return self._next(message)

    def say(self, message):
        self.said.append(message)


def make_ctx(**overrides):
    data = dict(
        instances={},
        versions=lambda: ["20.0", "19.0", "18.0"],
        port_in_use=lambda port: False,
        check_enterprise=lambda token: AccessResult(token == "good", "checked"),
        is_root=False,
        suggested_workers=5,
        resolve_domain=lambda d: {"1.2.3.4"},
        public_ip=lambda: "1.2.3.4",
    )
    data.update(overrides)
    return WizardContext(**data)


def test_local_community_flow():
    p = Scripted("local", "18.0", "community", "", "", False, "vscode")
    spec = run_wizard(p, make_ctx())
    assert (spec.mode, spec.version, spec.edition, spec.name) == ("local", "18.0", "community", "odoo18")
    assert (spec.http_port, spec.gevent_port, spec.ide) == (8069, 8072, "vscode")
    assert not any("domain" in q.lower() for q in p.asked)  # local never asks about domains
    assert not p.answers


def test_second_instance_gets_next_free_ports_and_name(make_instance):
    existing = make_instance("odoo18")
    p = Scripted("local", "18.0", "community", "", "", False, "none")
    spec = run_wizard(p, make_ctx(instances={"odoo18": existing}))
    assert spec.name == "odoo18-2"
    assert (spec.http_port, spec.gevent_port) == (8169, 8172)


def test_enterprise_with_valid_token():
    p = Scripted("local", "19.0", "enterprise", "good", "shop", "8069", False, "pycharm")
    spec = run_wizard(p, make_ctx())
    assert spec.edition == "enterprise" and spec.github_token == "good"


def test_enterprise_retry_then_success():
    p = Scripted("local", "19.0", "enterprise", "bad", True, "good", "shop", "8069", False, "none")
    spec = run_wizard(p, make_ctx())
    assert spec.edition == "enterprise"


def test_enterprise_failure_falls_back_to_community():
    p = Scripted("local", "19.0", "enterprise", "bad", False, "shop", "8069", False, "none")
    spec = run_wizard(p, make_ctx())
    assert spec.edition == "community" and spec.github_token is None
    assert any("Continuing with Community" in s for s in p.said)


def test_server_requires_root():
    with pytest.raises(WizardAbort, match="sudo"):
        run_wizard(Scripted("server"), make_ctx(is_root=False))


def test_server_with_domain_and_ssl():
    p = Scripted(
        "server",
        "19.0",
        "community",
        "prod",
        "8069",
        False,
        True,
        "ERP.Example.com",
        True,
        "me@example.com",
        "",
        True,
    )
    spec = run_wizard(p, make_ctx(is_root=True))
    assert spec.domain == "erp.example.com" and spec.ssl and spec.email == "me@example.com"
    assert spec.workers == 5 and spec.firewall
    assert any("points to this server" in s for s in p.said)
    assert spec.ide == "none"


def test_server_dns_mismatch_warns_and_defaults_ssl_off():
    p = Scripted(
        "server", "19.0", "community", "prod", "8069", False, True, "erp.example.com", False, "3", False
    )
    spec = run_wizard(p, make_ctx(is_root=True, public_ip=lambda: "9.9.9.9"))
    assert not spec.ssl and spec.workers == 3 and not spec.firewall
    assert any("SSL will fail" in s for s in p.said)


def test_server_without_domain():
    p = Scripted("server", "18.0", "community", "prod", "8069", False, False, "", True)
    spec = run_wizard(p, make_ctx(is_root=True))
    assert spec.domain is None and not spec.ssl
    assert any("SERVER-IP:8069" in s for s in p.said)


def test_presets_skip_questions():
    ctx = make_ctx(
        preset={
            "mode": "local",
            "version": "20.0",
            "edition": "community",
            "name": "x",
            "http_port": 9000,
            "demo": True,
            "ide": "none",
        }
    )
    p = Scripted()
    spec = run_wizard(p, ctx)
    assert (spec.version, spec.http_port, spec.gevent_port, spec.demo) == ("20.0", 9000, 9003, True)
    assert p.asked == []


def test_bad_presets_are_rejected(make_instance):
    with pytest.raises(WizardAbort, match="already exists"):
        run_wizard(
            Scripted(),
            make_ctx(
                instances={"x": make_instance("x")},
                preset={"mode": "local", "version": "18.0", "edition": "community", "name": "x"},
            ),
        )
    with pytest.raises(WizardAbort, match="already in use"):
        run_wizard(
            Scripted(),
            make_ctx(
                port_in_use=lambda p: p == 8069,
                preset={
                    "mode": "local",
                    "version": "18.0",
                    "edition": "community",
                    "name": "y",
                    "http_port": 8069,
                },
            ),
        )
