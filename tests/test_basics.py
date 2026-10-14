import base64
import io
import json
import urllib.error
import xml.etree.ElementTree as ET

import pytest

from odoobuddy import render, sources, versions
from odoobuddy.installer import suggested_workers, wkhtmltopdf_url
from odoobuddy.models import Instance, validate_name
from odoobuddy.ports import check_port, gevent_port_for, suggest_ports

# ------------------------------------------------------------------ versions


def test_parse_branches_keeps_stable_supported_versions():
    output = "\n".join(
        f"abc123\trefs/heads/{b}"
        for b in ["15.0", "16.0", "17.0", "saas-17.4", "18.0", "master", "19.0", "20.0", "19.0-foo"]
    )
    assert versions.parse_branches(output) == ["20.0", "19.0", "18.0", "17.0", "16.0"]


def test_available_versions_falls_back_when_offline(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(versions.subprocess, "run", boom)
    assert versions.available_versions() == versions.FALLBACK_VERSIONS


@pytest.mark.parametrize(
    ("version", "python"), [("16.0", "3.10"), ("17.0", "3.11"), ("18.0", "3.12"), ("21.0", "3.12")]
)
def test_python_for(version, python):
    assert versions.python_for(version) == python


def test_no_demo_flags_changed_in_odoo_19():
    assert versions.no_demo_flags("18.0") == ["--without-demo=all"]
    assert versions.no_demo_flags("19.0") == []
    assert versions.no_demo_flags("20.0") == []


# -------------------------------------------------------------------- models


@pytest.mark.parametrize("name", ["shop", "shop-18", "a", "odoo19-test"])
def test_valid_names(name):
    assert validate_name(name) == name


@pytest.mark.parametrize("name", ["", "Shop", "1shop", "shop_test", "shop-", "a" * 32, "my shop"])
def test_invalid_names(name):
    with pytest.raises(ValueError):
        validate_name(name)


def test_instance_roundtrip(make_instance):
    inst = make_instance(domain="erp.example.com", ssl=True, mode="server")
    data = json.loads(json.dumps(inst.to_dict()))
    assert Instance.from_dict(data) == inst
    assert inst.url == "https://erp.example.com"
    assert make_instance(mode="server", server_ip="198.51.100.7").url == "http://198.51.100.7:8069"
    assert make_instance(mode="server").url == "http://SERVER-IP:8069"  # registered before server_ip existed
    assert make_instance().url == "http://localhost:8069"
    assert inst.service == "odoo-shop" and inst.system_user == "odoo-shop"


# --------------------------------------------------------------------- ports


def test_suggest_ports_skips_registered_and_busy(make_instance):
    first = make_instance("a")  # 8069/8072
    busy = {8169}
    assert suggest_ports([first], lambda p: p in busy) == (8269, 8272)
    assert suggest_ports([], lambda p: False) == (8069, 8072)


def test_check_port_messages(make_instance):
    other = make_instance("hr", http_port=8169, gevent_port=8172)
    assert "used by instance 'hr'" in check_port(8172, [other], lambda p: False)
    assert "another program" in check_port(9000, [], lambda p: True)
    assert "between 1024" in check_port(80, [], lambda p: False)
    assert check_port(9000, [other], lambda p: False) == ""


def test_gevent_port_avoids_conflicts(make_instance):
    other = make_instance("hr", http_port=9000, gevent_port=9003)
    assert gevent_port_for(9000, [other], lambda p: False) == 9004


# ------------------------------------------------------------------ installer helpers


def test_suggested_workers():
    assert suggested_workers(cpus=4, ram_mb=16000) == 9
    assert suggested_workers(cpus=8, ram_mb=2048) == 3  # RAM-limited
    assert suggested_workers(cpus=1, ram_mb=1024) == 2  # never below 2


def test_wkhtmltopdf_url():
    assert wkhtmltopdf_url("noble", "amd64").endswith("wkhtmltox_0.12.6.1-3.jammy_amd64.deb")
    assert wkhtmltopdf_url("bookworm", "arm64").endswith(".bookworm_arm64.deb")
    assert wkhtmltopdf_url("fedora", "amd64") is None


# -------------------------------------------------------------------- render


def test_local_config(make_instance):
    inst = make_instance()
    conf = render.odoo_conf(inst, db_password="dbpw", master_password="master")
    assert "db_user = odoo_shop" in conf and "db_password = dbpw" in conf
    assert "http_port = 8069" in conf and "gevent_port = 8072" in conf
    assert "workers = 0" in conf and "dev_mode = reload,xml" in conf
    assert "logfile" not in conf and "proxy_mode" not in conf
    addons = next(line for line in conf.splitlines() if line.startswith("addons_path"))
    assert str(inst.instance_dir / "custom" / "addons") in addons


def test_server_config_with_enterprise(make_instance, tmp_path):
    inst = make_instance(mode="server", domain="erp.example.com", enterprise_dir=tmp_path / "ent")
    conf = render.odoo_conf(inst, db_password="x", master_password="y", workers=5)
    assert "proxy_mode = True" in conf and "admin_passwd = y" in conf
    assert "workers = 5" in conf
    assert "dbfilter" not in conf and "db_name" not in conf  # users create their own databases
    assert "http_interface = 127.0.0.1" in conf  # only reachable through nginx
    addons = next(line for line in conf.splitlines() if line.startswith("addons_path"))
    assert addons.split(" = ")[1].split(",")[0] == str(tmp_path / "ent")  # enterprise first


def test_nginx_names_are_unique_per_instance(make_instance):
    a = render.nginx_site(make_instance("shop", mode="server", domain="shop.example.com"))
    b = render.nginx_site(
        make_instance("hr-2", mode="server", domain="hr.example.com", http_port=8169, gevent_port=8172)
    )
    assert "upstream odoo_shop " in a and "upstream odoo_hr_2 " in b
    assert "$odoo_hr_2_connection_upgrade" in b
    assert "server 127.0.0.1:8172;" in b and "location /websocket" in b
    assert "server_name hr.example.com;" in b


def test_systemd_unit(make_instance):
    unit = render.systemd_unit(make_instance(mode="server"))
    assert "User=odoo-shop" in unit and "Restart=on-failure" in unit
    assert "-c " in unit and "odoo-bin" in unit


def test_ide_files_are_valid(make_instance):
    inst = make_instance()
    launch = json.loads(render.vscode_launch(inst))["configurations"]
    assert launch[0]["request"] == "attach" and launch[0]["connect"]["port"] == 28069
    assert launch[1]["python"] == str(inst.python_bin)
    task = json.loads(render.vscode_tasks(inst))["tasks"][0]
    assert task["runOptions"]["runOn"] == "folderOpen"
    assert task["options"]["env"] == {"ODOOBUDDY_DEBUG_PORT": "28069"}
    assert task["args"] == [str(inst.odoo_bin), "-c", str(inst.config_path)]
    startup = ET.fromstring(render.pycharm_startup(inst))
    conf = startup.find("./component[@name='ProjectStartupSharedConfiguration']/configurations/configuration")
    assert conf.get("id") == "Python.Odoo 18.0 (shop)" and conf.get("name") == "Odoo 18.0 (shop)"
    zed_tasks = json.loads(render.zed_tasks(inst))
    assert zed_tasks[0]["env"] == {"ODOOBUDDY_DEBUG_PORT": "28069"}
    zed_debug = json.loads(render.zed_debug(inst))
    assert zed_debug[0]["adapter"] == "Debugpy" and zed_debug[0]["connect"]["port"] == 28069
    assert "export ODOOBUDDY_DEBUG_PORT=28069" in render.start_script(inst)
    assert json.loads(render.vscode_settings(inst))["python.defaultInterpreterPath"] == str(inst.python_bin)
    root = ET.fromstring(render.pycharm_run_config(inst))
    options = {o.get("name"): o.get("value") for o in root.iter("option")}
    assert options["SDK_HOME"] == str(inst.python_bin)
    assert options["SCRIPT_NAME"] == str(inst.odoo_bin)


# ------------------------------------------------------------------- sources


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_enterprise_access_ok(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["auth"] = request.get_header("Authorization")
        return _Response(json.dumps({"full_name": "odoo/enterprise"}).encode())

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    result = sources.check_enterprise_access("  ghp_abc ")
    assert result.ok
    assert seen["auth"] == "Bearer ghp_abc"


@pytest.mark.parametrize(
    ("code", "text"), [(401, "invalid or expired"), (404, "no access"), (403, "refused"), (500, "HTTP 500")]
)
def test_enterprise_access_http_errors(monkeypatch, code, text):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(sources.ENTERPRISE_API, code, "x", {}, None)

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    result = sources.check_enterprise_access("tok")
    assert not result.ok and text in result.message


def test_enterprise_access_network_error_and_empty(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    assert "could not reach GitHub" in sources.check_enterprise_access("tok").message
    assert not sources.check_enterprise_access("   ").ok


def test_git_auth_env_hides_token_from_url():
    env = sources.git_auth_env("ghp_secret")
    header = env["GIT_CONFIG_VALUE_0"]
    assert header.startswith("Authorization: Basic ")
    assert base64.b64decode(header.split()[-1]).decode() == "x-access-token:ghp_secret"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


# -------------------------------------------------------------------- runner


def test_runner_streams_output_and_logs(tmp_path):
    from odoobuddy.system import Runner

    seen = []
    runner = Runner(log_file=tmp_path / "x.log", root=False, on_output=seen.append)
    result = runner.run(["sh", "-c", "printf 'one\\rtwo\\nthree\\n'; cat"], input="from-stdin\n")
    assert seen == ["one", "two", "three", "from-stdin"]  # \r progress updates become lines
    assert result.stdout.splitlines()[-1] == "from-stdin"
    assert "from-stdin" in (tmp_path / "x.log").read_text()


def test_runner_failure_keeps_tail(tmp_path):
    from odoobuddy.system import CommandError, Runner

    runner = Runner(root=False)
    with pytest.raises(CommandError) as info:
        runner.run(["sh", "-c", "seq 1 40; exit 3"])
    assert info.value.returncode == 3
    assert info.value.output.splitlines() == [str(i) for i in range(16, 41)]
    assert runner.run(["sh", "-c", "exit 2"], check=False).returncode == 2


def test_runner_never_waits_for_hidden_sudo_prompt():
    from odoobuddy.system import Runner

    runner = Runner(root=False, sudo_noninteractive=True)
    assert runner._prefix(sudo=True, as_user=None) == ["sudo", "-n"]
    assert runner._prefix(sudo=False, as_user="postgres")[:4] == ["sudo", "-n", "-u", "postgres"]
    assert Runner(root=True)._prefix(sudo=False, as_user="odoo-x") == ["runuser", "-u", "odoo-x", "--"]


def test_find_ide(monkeypatch, tmp_path):
    from odoobuddy import ide

    monkeypatch.setattr(ide.shutil, "which", lambda cmd: "/usr/bin/code" if cmd == "code" else None)
    toolbox = tmp_path / "Toolbox" / "scripts"
    toolbox.mkdir(parents=True)
    (toolbox / "pycharm").write_text("")
    monkeypatch.setitem(ide._PATTERNS, "pycharm", [str(toolbox / "pycharm*")])
    found = ide.detect_ides()
    assert found["vscode"] == "/usr/bin/code" and found["pycharm"] == str(toolbox / "pycharm")
    assert set(found) == {"vscode", "cursor", "windsurf", "vscodium", "pycharm", "zed"}
    monkeypatch.setitem(ide._PATTERNS, "pycharm", [])
    assert ide.find_ide("pycharm", desktop_dirs=[]) is None

    apps = tmp_path / "applications"
    apps.mkdir()
    (apps / "jetbrains-pycharm.desktop").write_text(
        '[Desktop Entry]\nName=PyCharm\nExec="/home/u/pycharm-2026.1/bin/pycharm" %f\n'
    )
    assert ide.find_ide("pycharm", desktop_dirs=[str(apps)]) == "/home/u/pycharm-2026.1/bin/pycharm"


def test_dry_run_shows_home_as_tilde(monkeypatch, tmp_path):
    from rich.console import Console

    from odoobuddy.system import Runner

    monkeypatch.setenv("HOME", str(tmp_path))
    console = Console(record=True, width=200)
    Runner(console=console, dry_run=True).run(["ls", str(tmp_path / "odoo" / "shop")])
    assert "$ ls ~/odoo/shop" in console.export_text()


def test_free_memory_and_disk(tmp_path):
    from odoobuddy.system import free_disk_gb, memory_available_mb

    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:  8000000 kB\nMemFree:  100000 kB\nMemAvailable:  2097152 kB\n")
    assert memory_available_mb(meminfo) == 2048
    assert memory_available_mb(tmp_path / "missing") == 0
    assert free_disk_gb(tmp_path / "not" / "created" / "yet") > 0  # measured on the nearest existing folder


@pytest.mark.parametrize(
    ("env", "mode"),
    [
        ({"DISPLAY": ":0"}, "local"),
        ({"WAYLAND_DISPLAY": "wayland-0", "SSH_CONNECTION": "1 2 3 22"}, "local"),
        ({"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"}, "server"),
        ({}, "server"),
    ],
)
def test_detect_mode(monkeypatch, env, mode):
    from odoobuddy.system import detect_mode

    for key in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_CONNECTION"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert detect_mode()[0] == mode


def test_backup_unit_name_cannot_clash_with_a_service():
    from odoobuddy.models import backup_unit_for, service_name_for

    assert backup_unit_for("shop") == "odoobuddy-backup-shop"
    assert backup_unit_for("shop") != service_name_for("shop-backup")


def test_prune_old_logs_keeps_recent_ones_and_other_files(tmp_path):
    import os
    import time

    from odoobuddy.system import prune_old_logs

    old, recent, other = (
        tmp_path / "backup-shop-1.log",
        tmp_path / "install-shop-2.log",
        tmp_path / "notes.txt",
    )
    for path in (old, recent, other):
        path.write_text("x")
    long_ago = time.time() - 31 * 86400
    for path in (old, other):
        os.utime(path, (long_ago, long_ago))
    assert prune_old_logs(tmp_path, 30) == [old]
    assert recent.exists() and other.exists()
    assert prune_old_logs(tmp_path / "missing", 30) == []
