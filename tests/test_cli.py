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


def test_remove_requires_typing_the_name(isolated, make_instance):
    isolated.add(make_instance("shop"))
    result = runner.invoke(app, ["remove", "shop"], input="wrong\n")
    assert result.exit_code == 1 and "nothing was changed" in result.output
    assert isolated.get("shop") is not None


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
    assert "Install summary" in result.output and "Dry run: nothing was changed" in result.output
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
    monkeypatch.setattr(
        "odoobuddy.cli.QuestionaryPrompter.confirm",
        lambda self, message, default: answers.append(message) or False,
    )
    cancelled = runner.invoke(app, INSTALL_SHOP)
    assert answers == ["Install anyway, at your own risk?"], cancelled.output
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
