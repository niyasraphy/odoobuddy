from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from odoobuddy import __version__
from odoobuddy.cli import app, rewrite_args
from odoobuddy.layout import Layout
from odoobuddy.registry import Registry

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOOBUDDY_ROOT", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.chdir(tmp_path)
    return Registry(Layout.under(tmp_path))


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and __version__ in result.output


def test_list_empty():
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0 and "No instances yet" in result.output


def test_list_info_use(isolated, make_instance):
    isolated.add(make_instance("shop"))
    isolated.add(make_instance("hr", version="19.0", http_port=8169, gevent_port=8172))

    result = runner.invoke(app, ["list"])
    assert "shop" in result.output and "hr" in result.output and "19.0" in result.output

    result = runner.invoke(app, ["use", "hr"])
    assert result.exit_code == 0 and "Now working with: hr · Odoo 19.0" in result.output

    result = runner.invoke(app, ["info"])  # uses the current instance
    assert "▶ hr" in result.output and "8169 web, 8172 websocket" in result.output
    assert "/web/database/manager" in result.output


def test_unknown_instance_lists_known_ones(isolated, make_instance):
    isolated.add(make_instance("shop"))
    result = runner.invoke(app, ["info", "nope"])
    assert result.exit_code == 1 and "Known instances: shop" in result.output
    result = runner.invoke(app, ["use", "nope"])
    assert result.exit_code == 1


def test_remove_asks_yes_no_for_development(isolated, make_instance):
    isolated.add(make_instance("shop"))
    result = runner.invoke(app, ["remove", "shop"], input="n\n")
    assert result.exit_code == 1 and "nothing was changed" in result.output
    assert isolated.get("shop") is not None


def test_remove_production_requires_typing_the_name(isolated, make_instance, monkeypatch):
    monkeypatch.setattr("odoobuddy.cli.is_root", lambda: True)
    isolated.add(make_instance("prod", mode="server"))
    result = runner.invoke(app, ["remove", "prod"], input="y\n")
    assert result.exit_code == 1 and "name did not match" in result.output
    assert isolated.get("prod") is not None


def test_install_dry_run_changes_nothing(tmp_path, monkeypatch):
    result = runner.invoke(
        app,
        [
            "install",
            "--mode",
            "local",
            "--version",
            "18.0",
            "--edition",
            "community",
            "--name",
            "shop",
            "--port",
            "18069",
            "--ide",
            "none",
            "--no-repos",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        "Review: nothing is installed yet" in result.output
        and "Dry run: nothing was changed" in result.output
    )
    assert not (tmp_path / "home" / "odoo" / "shop").exists()


def test_install_rejects_bad_option():
    result = runner.invoke(app, ["install", "--mode", "cloud", "--dry-run"])
    assert result.exit_code == 1 and "--mode must be one of" in result.output


def test_install_starts_the_wizard(monkeypatch):
    seen = {}

    def fake_wizard(prompter, ctx):
        seen["preset"] = ctx.preset
        from odoobuddy.wizard import WizardAbort

        raise WizardAbort("stop here")

    monkeypatch.setattr("odoobuddy.cli.run_wizard", fake_wizard)
    result = runner.invoke(app, ["install"])
    assert "stop here" in result.output
    assert seen["preset"] == {}  # nothing pre-answered


def test_tab_completion_for_commands_and_instance_names(isolated, make_instance, tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    isolated.add(make_instance("shop"))
    isolated.add(make_instance("hr", http_port=8169, gevent_port=8172))
    script = Path(sys.executable).parent / "odoobuddy"
    env = {**os.environ, "ODOOBUDDY_ROOT": str(tmp_path), "_ODOOBUDDY_COMPLETE": "complete_bash"}

    def complete(words, cword):
        env.update(COMP_WORDS=words, COMP_CWORD=str(cword))
        return subprocess.run([str(script)], env=env, capture_output=True, text=True).stdout.split()

    assert complete("odoobuddy st", 1) == ["status", "start", "stop"]
    assert complete("odoobuddy start ", 2) == ["hr", "shop"]
    assert complete("odoobuddy logs s", 2) == ["shop"]


def test_install_from_a_script_needs_yes():
    args = [
        "install",
        "--mode",
        "local",
        "--version",
        "18.0",
        "--edition",
        "community",
        "--name",
        "shop",
        "--port",
        "18069",
        "--run-with",
        "terminal",
        "--no-repos",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 1 and "add --yes" in result.output


def test_help_spellings():
    assert rewrite_args(["help"], set()) == ["--help"]
    assert rewrite_args(["help", "install"], set()) == ["install", "--help"]
    assert rewrite_args(["help", "start"], {"help"}) == ["start", "help"]  # an instance called help
    for args in (["-h"], ["help"], ["help", "backup"], ["backup", "-h"]):
        result = runner.invoke(app, rewrite_args(args, set()))
        assert result.exit_code == 0 and "Usage" in result.output, args


def test_status(isolated, make_instance):
    isolated.add(make_instance("shop"))
    isolated.add(make_instance("prod", mode="server", http_port=8169, gevent_port=8172))
    every = runner.invoke(app, ["status"])
    assert every.exit_code == 0 and "shop" in every.output and "prod" in every.output
    one = runner.invoke(app, ["status", "shop"])
    assert one.exit_code == 0 and "stopped" in one.output and "odoobuddy start shop" in one.output
    assert rewrite_args(["shop", "status"], {"shop"}) == ["status", "shop"]


def test_no_command_welcomes(isolated, make_instance, monkeypatch):
    # With an empty list, click falls back to sys.argv (pytest's own arguments).
    monkeypatch.setattr("sys.argv", ["odoobuddy"])
    monkeypatch.setattr("odoobuddy.cli.run_wizard", lambda *a, **k: pytest.fail("wizard started"))
    first = runner.invoke(app, [])
    assert first.exit_code == 0 and "Welcome to odoobuddy" in first.output
    assert "no Odoo instances yet" in first.output and "odoobuddy install" in first.output
    isolated.add(make_instance("shop"))
    again = runner.invoke(app, [])
    assert "Your instances" in again.output and "shop" in again.output


INSTALL_SHOP = [
    "install", "--mode", "local", "--version", "18.0", "--edition", "community", "--name", "shop",
    "--port", "18069", "--run-with", "terminal", "--no-repos",
]  # fmt: skip


def test_small_machine_warns_and_lets_the_user_decide(isolated, monkeypatch):
    monkeypatch.setattr("odoobuddy.cli.memory_available_mb", lambda: 300)
    dry = runner.invoke(app, [*INSTALL_SHOP, "--dry-run", "--yes"])
    assert "may be too small" in dry.output and "Free memory: 0.3 GB" in dry.output

    answers = []
    # CliRunner replaces sys.stdin, so pretend to be in a terminal through the module's view of sys.
    monkeypatch.setattr(
        "odoobuddy.cli.sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True), argv=[])
    )
    monkeypatch.setattr("odoobuddy.cli.QuestionaryPrompter.select", lambda self, *a, **k: "install")
    paused = []
    monkeypatch.setattr("odoobuddy.cli.QuestionaryPrompter.pause", lambda self, *a: paused.append(a))
    monkeypatch.setattr(
        "odoobuddy.cli.QuestionaryPrompter.confirm",
        lambda self, message, default: answers.append(message) or False,
    )
    cancelled = runner.invoke(app, INSTALL_SHOP)
    assert answers == ["Install anyway, at your own risk?"], cancelled.output
    assert "How this works" in cancelled.output and "Nothing is installed yet" in cancelled.output
    # Two stops before the questions: after the machine's details, and after "How this works".
    assert paused == [(), ("Press Enter to start the questions",)]
    assert cancelled.exit_code == 1 and "Cancelled, nothing was changed" in cancelled.output


def test_credits_and_support(isolated, monkeypatch):
    version = runner.invoke(app, ["--version"])
    assert "by Niyas Raphy" in version.output and "github.com/niyasraphy/odoobuddy" in version.output
    monkeypatch.setattr("sys.argv", ["odoobuddy"])
    welcome = runner.invoke(app, [])
    assert "Welcome to odoobuddy" in welcome.output and "by Niyas Raphy" in welcome.output
    assert "odoobuddy support" in welcome.output
    support = runner.invoke(app, ["support"])
    assert support.exit_code == 0
    assert "niyasraphyk@gmail.com" in support.output and "/issues" in support.output


def test_backup_all_and_nightly(isolated, make_instance, monkeypatch):
    isolated.add(make_instance("shop"))
    monkeypatch.setattr("odoobuddy.operations.list_databases", lambda runner, inst: [])
    empty = runner.invoke(app, ["backup", "shop", "--all", "--keep", "7"])
    assert empty.exit_code == 0 and "no databases yet" in empty.output
    local = runner.invoke(app, ["backup", "shop", "--nightly"])
    assert local.exit_code == 1 and "for server instances" in local.output


def test_offer_tab_completion(tmp_path, monkeypatch, capsys):
    from odoobuddy import cli
    from odoobuddy.system import completion_installed, login_shell

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.delenv("SUDO_USER", raising=False)
    assert login_shell() == "bash" and not completion_installed("bash")

    class Yes:
        asked = 0

        def confirm(self, message, default=True):
            Yes.asked += 1
            return True

    cli._offer_completion(Yes())  # type: ignore[arg-type]
    assert Yes.asked == 1 and completion_installed("bash")
    explained = capsys.readouterr().out
    assert "odoobuddy start" in explained and "one line to ~/.bashrc" in explained
    assert "source '" in (tmp_path / ".bashrc").read_text()
    cli._offer_completion(Yes())  # type: ignore[arg-type]
    assert Yes.asked == 1  # already on: not asked again

    monkeypatch.setenv("SHELL", "/bin/tcsh")
    assert login_shell() is None

    # Under sudo: not asked (it would be set up for root), but told how to turn it on.
    monkeypatch.setenv("SUDO_USER", "someone")
    cli._offer_completion(Yes())  # type: ignore[arg-type]
    assert Yes.asked == 1 and "run as your own user" in capsys.readouterr().out


def test_summary_marks_changed_settings():
    from dataclasses import replace

    from odoobuddy import ui
    from odoobuddy.models import InstallSpec

    spec = InstallSpec(name="shop", version="19.0", mode="local", edition="community", purpose="development")
    console = ui.Console(width=120)
    with console.capture() as first:
        console.print(ui.summary(spec))
    assert "Review: nothing is installed yet" in first.get() and "changed" not in first.get()
    with console.capture() as edited:
        console.print(ui.summary(replace(spec, http_port=8070), spec))
    lines = [line for line in edited.get().splitlines() if "changed" in line]
    assert len(lines) == 1 and "8070" in lines[0]


def test_review_offers_install_and_dry_run_separately(isolated, monkeypatch):
    monkeypatch.setattr(
        "odoobuddy.cli.sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True), argv=[])
    )
    monkeypatch.setattr("odoobuddy.cli.QuestionaryPrompter.pause", lambda self, *a: None)
    menus = []

    def choose(*answers):
        queue = list(answers)

        def select(self, message, choices, default=None, descriptions=None):
            menus.append((message, [value for value, _ in choices], default))
            return queue.pop(0)

        monkeypatch.setattr("odoobuddy.cli.QuestionaryPrompter.select", select)

    # Started with --dry-run: both options are there, Install now preselected; Dry run changes nothing.
    choose("dry-run")
    dry = runner.invoke(app, [*INSTALL_SHOP, "--dry-run"])
    assert dry.exit_code == 0 and "Dry run: nothing was changed" in dry.output, dry.output
    message, values, default = menus[-1]
    assert message == "Ready to install?" and values[:2] == ["install", "dry-run"] and default == "install"
    assert isolated.get("shop") is None

    # A server install without sudo, after --dry-run: Install now shows the sudo command instead.
    monkeypatch.setattr("odoobuddy.cli.is_root", lambda: False)
    choose("install", "cancel")
    server = [
        "install", "--purpose", "test", "--version", "18.0", "--edition", "community", "--name", "shop",
        "--port", "18069", "--no-domain", "--workers", "1", "--no-repos", "--dry-run",
    ]  # fmt: skip
    result = runner.invoke(app, server)
    assert "needs sudo" in result.output and "sudo -E odoobuddy install" in result.output, result.output
    assert result.exit_code == 1 and "Cancelled, nothing was changed" in result.output
