import pytest
from typer.testing import CliRunner

from odooinstaller import __version__
from odooinstaller.cli import app
from odooinstaller.layout import Layout
from odooinstaller.registry import Registry

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOOINSTALLER_ROOT", str(tmp_path))
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
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Install summary" in result.output and "Dry run: nothing was changed" in result.output
    assert not (tmp_path / "home" / "odoo" / "shop").exists()


def test_install_rejects_bad_option():
    result = runner.invoke(app, ["install", "--mode", "cloud", "--dry-run"])
    assert result.exit_code == 1 and "--mode must be one of" in result.output


def test_no_command_starts_the_wizard(monkeypatch):
    seen = {}

    def fake_wizard(prompter, ctx):
        seen["preset"] = ctx.preset
        from odooinstaller.wizard import WizardAbort

        raise WizardAbort("stop here")

    monkeypatch.setattr("odooinstaller.cli.run_wizard", fake_wizard)
    # With an empty list, click falls back to sys.argv (pytest's own arguments).
    monkeypatch.setattr("sys.argv", ["odooinstaller"])
    result = runner.invoke(app, [])
    assert "stop here" in result.output
    assert seen["preset"] == {"own_source": False}  # nothing pre-answered
