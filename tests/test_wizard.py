from collections import deque

import pytest

from odoobuddy.sources import AccessResult
from odoobuddy.wizard import (
    EDITING,
    WizardAbort,
    WizardContext,
    clean_domain,
    domain_problem,
    planned_steps,
    run_wizard,
)


class Scripted:
    """Answers questions in order, and records what was asked and said."""

    def __init__(self, *answers):
        self.answers = deque(answers)
        self.asked: list[str] = []
        self.said: list[str] = []
        self.descriptions: dict[str, dict[str, str]] = {}
        self.steps: list[tuple[int, int]] = []

    def _next(self, message):
        self.asked.append(message)
        if not self.answers:
            raise AssertionError(f"unexpected question: {message}")
        return self.answers.popleft()

    def select(self, message, choices, default=None, descriptions=None):
        self.descriptions[message] = descriptions or {}
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

    def progress(self, step, total):
        self.steps.append((step, total))


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
    p = Scripted("development", "18.0", "community", "", "vscode")
    spec = run_wizard(p, make_ctx())
    assert (spec.mode, spec.version, spec.edition, spec.name) == ("local", "18.0", "community", "odoo18")
    assert (spec.http_port, spec.gevent_port, spec.ide) == (8069, 8072, "vscode")
    assert not any("domain" in q.lower() for q in p.asked)  # local never asks about domains
    assert not p.answers


def test_step_counter_and_explanations():
    p = Scripted("development", "18.0", "community", "", "vscode")
    run_wizard(p, make_ctx())
    # Purpose is step 1 (asked before the total is known), then version, edition, name, run;
    # the counter is cleared at the end so later prompts show the plain "?".
    assert p.steps == [(2, 5), (3, 5), (4, 5), (5, 5), (0, 0)]
    purposes = p.descriptions["What is this Odoo for?"]
    assert "10 questions" in purposes["production"] and "6 questions" in purposes["test"]
    assert "5 questions" in purposes["development"]
    assert "token" in p.descriptions["Which edition?"]["enterprise"]
    run = next(d for message, d in p.descriptions.items() if "run Odoo" in message)
    assert "breakpoints" in run["vscode"] and "Ctrl+C" in run["terminal"]


def test_planned_steps_skip_answers_given_as_options():
    assert planned_steps(make_ctx(), "production") == [
        "purpose",
        "version",
        "edition",
        "name",
        "http_port",
        "repos",
        "domain",
        "workers",
        "firewall",
        "daily_backups",
    ]
    preset = {"purpose": "test", "version": "19.0", "name": "erp-test"}
    assert planned_steps(make_ctx(preset=preset), "test") == ["edition", "domain", "workers"]
    # Changing one setting from the summary: no counter.
    assert planned_steps(make_ctx(preset={EDITING: "version"}), "development") == []


def test_second_instance_gets_next_free_ports_and_name(make_instance):
    existing = make_instance("odoo18")
    p = Scripted("development", "18.0", "community", "", "background")
    spec = run_wizard(p, make_ctx(instances={"odoo18": existing}))
    assert spec.name == "odoo18-2"
    assert (spec.http_port, spec.gevent_port) == (8169, 8172)


def test_enterprise_with_valid_token():
    p = Scripted("development", "19.0", "enterprise", "good", "shop", "pycharm")
    spec = run_wizard(p, make_ctx())
    assert spec.edition == "enterprise" and spec.github_token == "good"


def test_enterprise_retry_then_success():
    p = Scripted("development", "19.0", "enterprise", "bad", True, "good", "shop", "background")
    spec = run_wizard(p, make_ctx())
    assert spec.edition == "enterprise"


def test_enterprise_failure_falls_back_to_community():
    p = Scripted("development", "19.0", "enterprise", "bad", False, "shop", "background")
    spec = run_wizard(p, make_ctx())
    assert spec.edition == "community" and spec.github_token is None
    assert any("Continuing with Community" in s for s in p.said)


def test_server_requires_root():
    with pytest.raises(WizardAbort, match="sudo"):
        run_wizard(Scripted("production"), make_ctx(is_root=False))


def test_server_with_domain_and_ssl():
    p = Scripted(
        "production",
        "19.0",
        "community",
        "prod",
        "8069",
        False,
        "ERP.Example.com",
        True,
        "me@example.com",
        "",
        True,
        True,
    )
    spec = run_wizard(p, make_ctx(is_root=True))
    assert spec.domain == "erp.example.com" and spec.ssl and spec.email == "me@example.com"
    assert spec.workers == 5 and spec.firewall and spec.daily_backups
    assert any("points to this server" in s for s in p.said)
    assert spec.ide == "none"


SERVER_START = ("production", "19.0", "community", "prod", "8069", False, "erp.example.com")


def test_server_dns_not_ready_keep_domain_without_ssl():
    p = Scripted(*SERVER_START, "keep", False, "3", False, False, "keep")
    spec = run_wizard(p, make_ctx(is_root=True, public_ip=lambda: "9.9.9.9"))
    assert spec.domain == "erp.example.com"
    assert not spec.ssl and spec.workers == 3 and not spec.firewall
    assert any("not to this server (9.9.9.9)" in s for s in p.said)
    assert any("Type: [bold]A[/bold]" in s and "[bold]erp[/bold]" in s for s in p.said)


def test_server_dns_check_again_until_it_points_here():
    answers = deque([set(), {"9.9.9.9"}])  # no record, then the record the user just added
    p = Scripted(*SERVER_START, "check", True, "", "", True, True)
    ctx = make_ctx(is_root=True, public_ip=lambda: "9.9.9.9", resolve_domain=lambda d: answers.popleft())
    spec = run_wizard(p, ctx)
    assert spec.domain == "erp.example.com" and spec.ssl
    assert any("has no DNS 'A' record" in s for s in p.said)
    assert any("points to this server" in s for s in p.said)


def test_server_dns_not_ready_continue_without_domain():
    p = Scripted(*SERVER_START, "none", "", True, True)
    spec = run_wizard(p, make_ctx(is_root=True, resolve_domain=lambda d: set()))
    assert spec.domain is None and not spec.ssl
    assert not any("SSL certificate (HTTPS" in q for q in p.asked)


def test_dns_preset_domain_only_warns():
    from odoobuddy.models import InstallSpec
    from odoobuddy.wizard import _confirm_dns

    p = Scripted()
    spec = InstallSpec(
        mode="server", version="19.0", edition="community", name="prod", domain="erp.example.com"
    )
    assert _confirm_dns(p, make_ctx(resolve_domain=lambda d: set()), spec, ask=False) is False
    assert spec.domain == "erp.example.com" and not p.asked


def test_server_without_domain():
    p = Scripted("production", "18.0", "community", "prod", "8069", False, "", "", True, False, "keep")
    spec = run_wizard(p, make_ctx(is_root=True))
    assert spec.domain is None and not spec.ssl
    assert any("http://1.2.3.4:8069" in s for s in p.said)  # the server's own IP


def test_presets_skip_questions():
    ctx = make_ctx(
        preset={
            "mode": "local",
            "version": "20.0",
            "edition": "community",
            "name": "x",
            "http_port": 9000,
            "ide": "none",
            "repos": [],
        }
    )
    p = Scripted()
    spec = run_wizard(p, ctx)
    assert (spec.version, spec.http_port, spec.gevent_port) == ("20.0", 9000, 9003)
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


def test_installed_ide_is_labelled_and_preselected():
    seen = {}

    class Recorder(Scripted):
        def select(self, message, choices, default=None, descriptions=None):
            if "run Odoo" in message:
                seen["choices"], seen["default"] = dict(choices), default
            return super().select(message, choices, default, descriptions)

    p = Recorder("development", "18.0", "community", "", "pycharm")
    ctx = make_ctx(installed_ides={"vscode": None, "pycharm": "/snap/bin/pycharm-community"})
    spec = run_wizard(p, ctx)
    assert spec.ide == "pycharm"
    assert seen["default"] == "pycharm"
    assert seen["choices"]["pycharm"].endswith("✓ installed")
    assert next(iter(seen["choices"])) == "pycharm"  # installed editors listed first
    assert {"terminal", "background"} <= set(seen["choices"])
    assert spec.run_mode == "editor"
    assert seen["choices"]["vscode"].endswith("not found on this computer")
    assert not any("demo" in q.lower() for q in p.asked)


def test_editing_one_setting_only_asks_that_question():
    from odoobuddy.wizard import answers_without, editable_fields

    first = run_wizard(Scripted("development", "18.0", "community", "shop", "vscode"), make_ctx())
    labels = dict(editable_fields(first))
    assert labels["http_port"] == "Port: 8069" and "domain" not in labels

    p = Scripted("9069")  # only the port is asked again
    edited = run_wizard(p, make_ctx(preset=answers_without(first, "http_port")))
    assert (edited.name, edited.version, edited.ide, edited.http_port) == ("shop", "18.0", "vscode", 9069)
    assert len(p.asked) == 1 and "port" in p.asked[0]


def test_switching_to_server_asks_the_server_questions():
    from odoobuddy.wizard import answers_without

    first = run_wizard(Scripted("development", "19.0", "community", "shop", "pycharm"), make_ctx())
    p = Scripted("production", "erp.example.com", True, "", "", False, True, "keep")
    edited = run_wizard(p, make_ctx(is_root=True, preset=answers_without(first, "purpose")))
    assert edited.mode == "server" and edited.domain == "erp.example.com" and edited.ssl
    assert edited.ide == "none" and edited.name == "shop"


def test_changing_edition_to_enterprise_asks_for_token():
    from odoobuddy.wizard import answers_without

    first = run_wizard(Scripted("development", "19.0", "community", "shop", "background"), make_ctx())
    edited = run_wizard(Scripted("enterprise", "good"), make_ctx(preset=answers_without(first, "edition")))
    assert edited.edition == "enterprise" and edited.github_token == "good"


def test_server_email_left_empty_is_not_asked_again():
    from odoobuddy.wizard import answers_without

    p = Scripted(
        "production",
        "19.0",
        "community",
        "prod",
        "8069",
        False,
        "erp.example.com",
        True,
        "",
        "",
        True,
        True,
    )
    first = run_wizard(p, make_ctx(is_root=True))
    assert first.email is None
    q = Scripted("4")
    edited = run_wizard(q, make_ctx(is_root=True, preset=answers_without(first, "workers")))
    assert edited.workers == 4 and len(q.asked) == 1


def test_server_without_domain_needs_free_internal_ports():
    p = Scripted("production", "18.0", "community", "prod", "8069", False, "")
    with pytest.raises(WizardAbort, match="internal port 18069"):
        run_wizard(p, make_ctx(is_root=True, port_in_use=lambda port: port == 18069))


@pytest.mark.parametrize(
    ("answer", "ide", "mode"),
    [("terminal", "none", "terminal"), ("background", "none", "background"), ("zed", "zed", "editor")],
)
def test_run_mode_choices(answer, ide, mode):
    spec = run_wizard(Scripted("development", "19.0", "community", "shop", answer), make_ctx())
    assert (spec.ide, spec.run_mode) == (ide, mode)


def test_debug_port_must_be_free():
    p = Scripted("development", "19.0", "community", "shop", "vscode")
    with pytest.raises(WizardAbort, match="debugger needs port 28069"):
        run_wizard(p, make_ctx(port_in_use=lambda port: port == 28069))


LOCAL_START = ("development", "18.0", "community", "")


def test_missing_editor_user_installs_it_then_check_again():
    ctx = make_ctx(installed_ides={"vscode": None}, find_ide=lambda ide: "/usr/bin/code")
    p = Scripted(*LOCAL_START, "vscode", "check")
    spec = run_wizard(p, ctx)
    assert (spec.ide, spec.run_mode, spec.install_editor) == ("vscode", "editor", False)
    assert any("Visual Studio Code is not installed" in s or "VS Code is not installed" in s for s in p.said)
    assert any("Please" in s and "code.visualstudio.com" in s and "waits for you" in s for s in p.said)
    assert any("found (/usr/bin/code)" in s for s in p.said)


def test_missing_editor_odoobuddy_installs_it():
    from odoobuddy.ide import EditorInstall

    ctx = make_ctx(
        installed_ides={"zed": None},
        editor_install=lambda ide: EditorInstall(["sh", "-c", "x"], False, "official installer"),
    )
    p = Scripted(*LOCAL_START, "zed", "install")
    spec = run_wizard(p, ctx)
    assert (spec.ide, spec.install_editor) == ("zed", True)
    assert not any("Please" in s for s in p.said)  # no manual steps when we install it


def test_missing_editor_choose_another_or_use_later():
    ctx = make_ctx(installed_ides={"vscode": "/usr/bin/code", "zed": None})
    p = Scripted(*LOCAL_START, "zed", "check", "back", "terminal")
    spec = run_wizard(p, ctx)
    assert (spec.ide, spec.run_mode) == ("none", "terminal")
    assert any("still not found" in s for s in p.said)

    p = Scripted(*LOCAL_START, "zed", "later")
    assert run_wizard(p, make_ctx(installed_ides={"zed": None})).ide == "zed"


def test_installed_editor_asks_nothing_more():
    p = Scripted(*LOCAL_START, "vscode")
    assert run_wizard(p, make_ctx(installed_ides={"vscode": "/usr/bin/code"})).ide == "vscode"


def test_install_editor_survives_changing_another_setting():
    from odoobuddy.models import InstallSpec
    from odoobuddy.wizard import answers_without

    spec = InstallSpec(
        name="a", version="18.0", mode="local", ide="zed", run_mode="editor", install_editor=True
    )
    assert answers_without(spec, "http_port")["install_editor"] is True
    assert "install_editor" not in answers_without(spec, "ide")


def test_purpose_question_with_detection():
    seen = {}

    class Spy(Scripted):
        def select(self, message, choices, default=None, descriptions=None):
            if message.startswith("What is this Odoo for"):
                seen.update(choices=choices, default=default)
            return super().select(message, choices, default, descriptions)

    with pytest.raises(WizardAbort):  # stop right after the first question (not root)
        run_wizard(Spy("production"), make_ctx(detected_mode="server", detected_reason="connected over SSH"))
    # On a server, Development is left out of the menu, with the option to choose it anyway.
    assert [value for value, _ in seen["choices"]] == ["production", "test"]
    assert seen["default"] == "production" and "detected: connected over SSH" in seen["choices"][0][1]


def test_too_many_workers_must_be_lowered():
    from odoobuddy.wizard import _validate_workers

    check = _validate_workers(3)
    assert check("3") == "" and check("1") == ""
    assert "at most 3" in check("4") and check("0") and check("x")
    preset = dict(mode="server", version="19.0", edition="community", name="prod", http_port=8069)
    ctx = make_ctx(is_root=True, max_workers=3, preset={**preset, "repos": [], "domain": None, "workers": 8})
    with pytest.raises(WizardAbort, match="--workers 8 is more than this server can run: at most 3"):
        run_wizard(Scripted(), ctx)


def test_test_instance_follows_production_and_stays_small(make_instance):
    prod = make_instance("erp", version="19.0", mode="server", purpose="production")
    seen = {}

    class Spy(Scripted):
        def select(self, message, choices, default=None, descriptions=None):
            seen[message] = default
            return super().select(message, choices, default, descriptions)

        def text(self, message, default="", validate=None):
            seen[message] = default
            return super().text(message, default, validate)

    # purpose, version, edition, name (default), domain (none), workers (default)
    p = Spy("test", "19.0", "community", "", "", "")
    spec = run_wizard(p, make_ctx(is_root=True, instances={"erp": prod}, max_workers=6))
    assert seen["Which Odoo version?"] == "19.0" and spec.name == "erp-test" and spec.role == "test"
    assert spec.workers == 2 and not spec.firewall and not spec.daily_backups
    assert not any("firewall" in q.lower() or "back up" in q.lower() or "port" in q.lower() for q in p.asked)
    assert not any("Production check" in m for m in p.said)


def test_production_check_applies_the_recommendations():
    # no SSL, no backups, no firewall, 5 workers with 1 GB free
    p = Scripted(*SERVER_START, "keep", False, "", False, False, "apply")
    spec = run_wizard(p, make_ctx(is_root=True, available_mb=1024, public_ip=lambda: "9.9.9.9"))
    assert spec.ssl and spec.daily_backups and spec.firewall and spec.workers == 2
    assert any("Production check" in m for m in p.said)


def test_production_check_only_reports_when_not_interactive():
    p = Scripted(*SERVER_START, "keep", False, "", False, False)  # no final choice is asked
    spec = run_wizard(p, make_ctx(is_root=True, interactive=False, public_ip=lambda: "9.9.9.9"))
    assert not spec.firewall and not spec.daily_backups
    assert any("Firewall off" in m for m in p.said)


def test_production_check_passes_quietly():
    p = Scripted(*SERVER_START, True, "", "", True, True)
    run_wizard(p, make_ctx(is_root=True))
    assert any("✓ Production check" in m for m in p.said)


def test_old_mode_preset_still_works():
    ctx = make_ctx(
        is_root=True, preset={"mode": "server", "version": "19.0", "edition": "community", "name": "p"}
    )
    p = Scripted("8069", False, "", "", True, True)
    assert run_wizard(p, ctx).role == "production"


def test_domain_answers_are_cleaned_or_explained():
    assert clean_domain("  https://ERP.Example.com/web/ ") == "erp.example.com"
    assert domain_problem("", "erp.example.com") == ""  # Enter: no domain
    assert domain_problem("https://erp.example.com/", "erp.example.com") == ""
    assert "no spaces" in domain_problem("erp example.com", "erp.example.com")
    assert "IP address" in domain_problem("37.231.92.81", "erp.example.com")
    assert "not complete" in domain_problem("erp", "erp.example.com")
    assert "not a valid domain" in domain_problem("erp_shop.com", "erp.example.com")


def test_development_offered_on_a_desktop_and_by_option_on_a_server():
    p = Scripted("development", "18.0", "community", "", "vscode")
    assert run_wizard(p, make_ctx(detected_mode="local")).role == "development"
    preset = {"purpose": "development"}
    p = Scripted("18.0", "community", "", "vscode")
    assert run_wizard(p, make_ctx(detected_mode="server", preset=preset)).role == "development"
