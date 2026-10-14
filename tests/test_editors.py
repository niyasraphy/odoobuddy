import json

import pytest

from odoobuddy import editors

from .conftest import RecordingRunner


@pytest.mark.parametrize(
    ("ide", "files"),
    [
        ("vscode", [".vscode/launch.json", ".vscode/tasks.json", ".vscode/settings.json"]),
        ("cursor", [".vscode/launch.json", ".vscode/tasks.json"]),
        ("pycharm", [".idea/startup.xml", ".idea/runConfigurations/Odoo_shop.xml", ".idea/shop.iml"]),
        ("zed", [".zed/tasks.json", ".zed/debug.json", ".zed/settings.json"]),
    ],
)
def test_configure_writes_editor_files(make_instance, ide, files):
    inst = make_instance("shop", ide=ide, run_mode="editor")
    editors.configure(RecordingRunner(), inst)
    for name in files:
        assert (inst.instance_dir / name).is_file(), name
    others = {".vscode", ".idea", ".zed"} - {files[0].split("/")[0]}
    assert not any((inst.instance_dir / o).exists() for o in others)


def test_debug_hook_goes_into_site_packages(make_instance):
    inst = make_instance("shop")
    site = inst.venv_dir / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    editors.install_debug_hook(RecordingRunner(), inst)
    assert (site / "_odoobuddy_debug.pth").read_text() == "import _odoobuddy_debug\n"
    hook = (site / "_odoobuddy_debug.py").read_text()
    assert "ODOOBUDDY_DEBUG_PORT" in hook and "subProcess=False" in hook
    compile(hook, "_odoobuddy_debug.py", "exec")


def test_open_editor(make_instance, monkeypatch):
    inst = make_instance("shop", ide="zed", run_mode="editor")
    inst.instance_dir.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(editors, "find_ide", lambda ide: "/opt/zed/bin/zed --new")
    monkeypatch.setattr(editors.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    monkeypatch.setenv("DISPLAY", ":0")
    assert editors.open_editor(inst) == ["/opt/zed/bin/zed", "--new", str(inst.instance_dir)]
    assert calls[0][1]["start_new_session"] is True

    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with pytest.raises(RuntimeError, match="no graphical session"):
        editors.open_editor(inst)

    monkeypatch.setattr(editors, "find_ide", lambda ide: None)
    with pytest.raises(RuntimeError, match="Zed was not found"):
        editors.open_editor(inst)
    assert editors.manual_open_hint(inst) == f"zed {inst.instance_dir}"


def test_open_editor_requires_an_editor(make_instance):
    with pytest.raises(RuntimeError, match="no editor"):
        editors.open_editor(make_instance("shop"))


def test_run_command_sets_debug_port(make_instance):
    from odoobuddy import operations

    cmd, env = operations.run_command(make_instance("shop", http_port=8169))
    assert cmd[1].endswith("odoo-bin") and env["ODOOBUDDY_DEBUG_PORT"] == "28169"


def test_vscode_settings_allow_folder_open_task(make_instance):
    from odoobuddy import render

    settings = json.loads(render.vscode_settings(make_instance("shop")))
    assert settings["task.allowAutomaticTasks"] == "on"


def test_ensure_debug_hook_upgrades_old_instances(make_instance):
    inst = make_instance("old")
    site = inst.venv_dir / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    inst.python_bin.parent.mkdir(parents=True)
    inst.python_bin.write_text("")
    assert editors.ensure_debug_hook(RecordingRunner(), inst) is True
    assert editors.ensure_debug_hook(RecordingRunner(), inst) is False  # already current
    (site / "_odoobuddy_debug.py").write_text("old version")
    assert editors.ensure_debug_hook(RecordingRunner(), inst) is True


def test_help_says_when_the_editor_is_not_installed(make_instance, monkeypatch):
    from odoobuddy import ui

    inst = make_instance("shop", ide="zed", run_mode="editor")
    monkeypatch.setattr(ui, "find_ide", lambda ide: None)
    text = ui.ide_help(inst)
    assert "not installed yet" in text and "zed.dev/install.sh" in text and "used as-is" not in text
    monkeypatch.setattr(ui, "find_ide", lambda ide: "/x/zed")
    assert "your installed Zed is used as-is" in ui.ide_help(inst)


def test_after_install_explains_missing_editor(make_instance, monkeypatch):
    from odoobuddy import cli

    inst = make_instance("shop", ide="zed", run_mode="editor")
    monkeypatch.setattr(cli, "find_ide", lambda ide: None)
    message = cli._after_install(RecordingRunner(), inst)
    assert "Zed is not installed" in message and "odoobuddy open shop" in message
