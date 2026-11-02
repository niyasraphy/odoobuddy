import json
import zipfile

import pytest

from odoobuddy import operations, render
from odoobuddy.registry import MARKER_FILE
from odoobuddy.system import CommandError

from .conftest import RecordingRunner


@pytest.fixture
def configured(make_instance):
    inst = make_instance("shop")
    inst.config_path.parent.mkdir(parents=True)
    inst.config_path.write_text(
        "[options]\ndb_host = localhost\ndb_port = 5432\ndb_user = odoo_shop\ndb_password = pw\n"
    )
    filestore = inst.data_dir / "filestore" / "shop" / "ab"
    filestore.mkdir(parents=True)
    (filestore / "abcdef").write_bytes(b"attachment")
    return inst


def _fake_pg_dump(cmd):
    if "pg_dump" in cmd:
        target = cmd[cmd.index("--file") + 1]
        with open(target, "w") as fh:
            fh.write("-- dump\n")


def test_backup_creates_odoo_format_zip(configured, tmp_path):
    runner = RecordingRunner(on_run=_fake_pg_dump)
    path = operations.backup(runner, configured, "shop", tmp_path / "backups")
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
    assert {"dump.sql", "manifest.json", "filestore/ab/abcdef"} <= names
    assert manifest["db_name"] == "shop" and manifest["version"] == "18.0"
    assert path.stat().st_mode & 0o777 == 0o600
    assert runner.envs[0]["PGPASSWORD"] == "pw"
    assert "pw" not in runner.commands[0]


def test_restore_replaces_database_and_filestore(configured, tmp_path, monkeypatch):
    monkeypatch.setattr(operations, "status", lambda inst: operations.STATUS_STOPPED)
    archive = tmp_path / "b.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("dump.sql", "-- dump")
        zf.writestr("filestore/cd/new", "new file")
    runner = RecordingRunner()
    operations.restore(runner, configured, archive, "shop")
    cmds = runner.joined()
    assert cmds[0] == "dropdb --if-exists --force shop"
    assert cmds[1] == "createdb --owner odoo_shop shop"
    assert cmds[2].startswith("psql -v ON_ERROR_STOP=1")
    filestore = configured.data_dir / "filestore" / "shop"
    assert (filestore / "cd" / "new").read_text() == "new file"
    assert not (filestore / "ab").exists()


def test_restore_rejects_non_backups(configured, tmp_path):
    bad = tmp_path / "x.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(ValueError, match=r"no dump\.sql"):
        operations.restore(RecordingRunner(), configured, bad, "shop")
    not_zip = tmp_path / "x.txt"
    not_zip.write_text("hi")
    with pytest.raises(ValueError, match="not a zip"):
        operations.restore(RecordingRunner(), configured, not_zip, "shop")


def test_update_pulls_code_and_repos_and_upgrades_every_database(configured, monkeypatch):
    monkeypatch.setattr(operations, "status", lambda inst: operations.STATUS_STOPPED)
    monkeypatch.setattr(operations, "find_uv", lambda: "uv")
    monkeypatch.setattr(operations, "list_databases", lambda runner, inst: ["main", "test"])
    repo = configured.custom_dir / "web"
    (repo / ".git").mkdir(parents=True)
    (repo / "requirements.txt").write_text("requests\n")
    runner = RecordingRunner()
    warnings = operations.update(runner, configured, github_token=None)
    cmds = runner.joined()
    assert warnings == []
    assert sum("fetch --progress --depth 1 origin 18.0" in c for c in cmds) == 1
    assert any(c.endswith("gc --prune=now --quiet") for c in cmds)
    assert any(c.endswith(f"-C {repo} pull --ff-only") for c in cmds)
    assert any(c.startswith("uv pip install") and str(repo / "requirements.txt") in c for c in cmds)
    upgraded = [c.split(" -d ")[1].split()[0] for c in cmds if "-u all --stop-after-init" in c]
    assert upgraded == ["main", "test"]
    assert "addons_path = " in configured.config_path.read_text()


def test_remove_local_instance(registry, configured, layout, monkeypatch):
    monkeypatch.setattr(operations, "status", lambda inst: operations.STATUS_STOPPED)
    (configured.instance_dir / MARKER_FILE).write_text("shop")
    registry.add(configured)
    runner = RecordingRunner()
    operations.remove(runner, registry, layout, configured)
    assert not configured.instance_dir.exists() and not configured.data_dir.exists()
    assert registry.get("shop") is None
    assert any("DROP ROLE" in (i or "") for i in runner.inputs)
    assert not any("systemctl" in c or "certbot" in c for c in runner.joined())


def test_remove_server_instance(registry, make_instance, layout, monkeypatch):
    monkeypatch.setattr(operations, "status", lambda inst: operations.STATUS_STOPPED)
    inst = make_instance("prod", mode="server", domain="erp.example.com", ssl=True)
    unit = layout.systemd_dir / "odoo-prod.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("x")
    registry.add(inst)
    runner = RecordingRunner(root=True)
    operations.remove(runner, registry, layout, inst)
    cmds = runner.joined()
    assert "certbot delete --non-interactive --cert-name erp.example.com" in cmds
    assert "userdel odoo-prod" in cmds
    assert not unit.exists()


def test_logs_and_shell_commands(make_instance):
    local = make_instance()
    assert operations.logs_command(local, 50, True) == ["tail", "-n", "50", "-F", str(local.log_path)]
    cmd = operations.shell_command(local, "mydb")
    assert cmd[2:4] == ["shell", "-c"] and cmd[-2:] == ["-d", "mydb"]


def test_list_databases_hides_install_check(configured):
    class Runner(RecordingRunner):
        def run(self, cmd, **kwargs):
            result = super().run(cmd, **kwargs)
            result.stdout = "shop\nodoo_shop_install_check\nshop_copy\n"
            return result

    runner = Runner()
    assert operations.list_databases(runner, configured) == ["shop", "shop_copy"]
    assert runner.envs[0]["PGUSER"] == "odoo_shop"  # connects as the instance's own user


def test_doctor_reports_port_clash_and_missing_files(registry, make_instance, monkeypatch):
    monkeypatch.setattr(operations, "status", lambda inst: operations.STATUS_STOPPED)
    monkeypatch.setattr(operations, "port_in_use", lambda port: False)
    registry.add(make_instance("a"))
    registry.add(make_instance("b"))  # same ports as "a"
    checks = operations.doctor(registry)
    messages = [c.message for c in checks if not c.ok]
    assert any("port 8069 also used by a" in m for m in messages)
    assert any("config file missing" in m for m in messages)


def test_running_odoo_found_without_pid_file(make_instance, tmp_path):
    inst = make_instance("shop")
    proc = tmp_path / "proc"
    for pid, args in {
        "100": ["python", "/x/odoo-bin", "-c", str(inst.config_path)],
        "101": ["python", "/x/odoo-bin", "shell", "-c", str(inst.config_path)],
        "102": ["python", "/x/odoo-bin", "-c", "/other.conf"],
        "self": ["x"],
    }.items():
        (proc / pid).mkdir(parents=True)
        (proc / pid / "cmdline").write_bytes("\0".join(args).encode() + b"\0")
    assert operations._find_odoo_process(inst, proc) == 100


def test_nightly_backups_schedule_and_unschedule(layout, make_instance):
    inst = make_instance("prod", mode="server")
    runner = RecordingRunner(root=True)
    operations.schedule_backups(runner, layout, inst, keep=5)
    service, timer = operations.backup_units(layout, inst)
    assert service.name == "odoobuddy-backup-prod.service" and timer.name == "odoobuddy-backup-prod.timer"
    assert "-m odoobuddy backup prod --all --keep 5" in service.read_text()
    assert "OnCalendar=*-*-* 02:30" in timer.read_text() and "Persistent=true" in timer.read_text()
    assert "systemctl enable --now odoobuddy-backup-prod.timer" in runner.joined()
    assert operations.backups_scheduled(layout, inst)

    operations.schedule_backups(runner, layout, inst, keep=5, dest=layout.server_root / "zips")
    assert service.read_text().count("--to") == 1 and "/zips" in service.read_text()

    operations.unschedule_backups(runner, layout, inst)
    assert not service.exists() and not timer.exists() and not operations.backups_scheduled(layout, inst)
    assert "systemctl disable --now odoobuddy-backup-prod.timer" in runner.joined()


def test_remove_stops_nightly_backups_but_keeps_the_zips(registry, layout, make_instance, monkeypatch):
    monkeypatch.setattr(operations, "status", lambda inst: operations.STATUS_STOPPED)
    inst = make_instance("prod", mode="server")
    registry.add(inst)
    runner = RecordingRunner(root=True)
    operations.schedule_backups(runner, layout, inst)
    operations.remove(runner, registry, layout, inst)
    assert not operations.backup_units(layout, inst)[1].exists()
    assert not any(c.startswith("rm") and "backups" in c for c in runner.joined())


def test_prune_backups_keeps_the_newest(tmp_path, make_instance):
    inst = make_instance("shop")
    names = [f"shop_main_2026101{day}-023000.zip" for day in range(5)]
    for name in [*names, "shop_main_copy.zip", "shop_other_20261010-023000.zip"]:
        (tmp_path / name).write_text("zip")
    deleted = operations.prune_backups(inst, "main", tmp_path, keep=2)
    assert [p.name for p in deleted] == names[:3]
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == sorted([*names[3:], "shop_main_copy.zip", "shop_other_20261010-023000.zip"])


def test_doctor_wants_nightly_backups_for_production(registry, layout, make_instance, monkeypatch):
    monkeypatch.setattr(operations, "status", lambda inst: operations.STATUS_STOPPED)
    prod = make_instance("prod", mode="server")
    registry.add(prod)
    registry.add(make_instance("stage", mode="server", purpose="test", http_port=8169, gevent_port=8172))

    def backup_checks():
        return [c for c in operations.doctor(registry) if "backups" in c.message]

    assert [(c.subject, c.ok) for c in backup_checks()] == [("prod", False)]  # test instances are not checked
    operations.schedule_backups(RecordingRunner(root=True), layout, prod)
    assert [(c.subject, c.ok) for c in backup_checks()] == [("prod", True)]


def test_allow_database_updates_only_protected_instances(make_instance, tmp_path):
    class Runner_(RecordingRunner):
        def run(self, cmd, **kwargs):
            result = super().run(cmd, **kwargs)
            result.stdout = "erp\n"
            return result

    for hardened, expected in ((True, "dbfilter = ^(erp|new\\.db)$"), (False, "dbfilter = ^erp$")):
        inst = make_instance("shop", mode="server", purpose="production", hardened=hardened)
        inst.config_path.parent.mkdir(parents=True, exist_ok=True)
        inst.config_path.write_text("[options]\ndb_user = x\ndbfilter = ^erp$\n")
        operations.allow_database(Runner_(root=True), inst, "new.db")
        assert expected in inst.config_path.read_text()


CERTBOT_TAIL = """
    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/erp/fullchain.pem; # managed by Certbot
"""


def _protected_site(make_instance, layout, registry, allow=()):
    inst = make_instance("shop", mode="server", purpose="production", hardened=True, domain="erp.example.com")
    registry.add(inst)
    site = operations._site_path(layout, inst)
    site.parent.mkdir(parents=True, exist_ok=True)
    inst.rpc_allow = list(allow)
    site.write_text(render.nginx_site(inst) + CERTBOT_TAIL)
    return inst, site


def test_api_addresses_change_only_the_api_block(make_instance, layout, registry):
    inst, site = _protected_site(make_instance, layout, registry)
    runner = RecordingRunner(root=True)
    before = site.read_text()
    operations.set_api_allowed(runner, registry, layout, inst, ["203.0.113.10/32", "2001:db8::/32"])
    after = site.read_text()
    assert operations.api_allowed(layout, inst) == ["203.0.113.10/32", "2001:db8::/32"]
    block = after.split("(xmlrpc|jsonrpc)")[1].split("}")[0]
    assert block.index("allow 2001:db8::/32;") < block.index("deny all;")
    assert "managed by Certbot" in after and "proxy_pass" in block
    assert runner.joined() == ["nginx -t", "systemctl reload nginx"]
    assert registry.get("shop").rpc_allow == ["203.0.113.10/32", "2001:db8::/32"]
    operations.set_api_allowed(runner, registry, layout, inst, [])
    assert site.read_text() == before


def test_api_change_is_undone_when_nginx_rejects_it(make_instance, layout, registry):
    inst, site = _protected_site(make_instance, layout, registry, ["203.0.113.10/32"])
    before = site.read_text()
    with pytest.raises(CommandError):
        operations.set_api_allowed(RecordingRunner(root=True, fail_on="nginx -t"), registry, layout, inst, [])
    assert site.read_text() == before
    assert registry.get("shop").rpc_allow == []  # registered without the address in this test


def test_api_block_edited_by_hand(make_instance, layout, registry):
    inst, site = _protected_site(make_instance, layout, registry)
    text = site.read_text()
    block_end = text.index("deny all;", text.index("(xmlrpc|jsonrpc)"))
    edited = (
        text[:block_end] + "# partner\n        allow 198.51.100.7; # by hand\n        " + text[block_end:]
    )
    site.write_text(edited)
    assert operations.api_allowed(layout, inst) == ["198.51.100.7"]
    operations.set_api_allowed(RecordingRunner(root=True), registry, layout, inst, ["198.51.100.7"])
    assert "# partner" in site.read_text()
    site.write_text(text.replace("deny all;", "", 2))
    with pytest.raises(ValueError, match="edited by hand"):
        operations.api_allowed(layout, inst)
