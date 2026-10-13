import json
import zipfile

import pytest

from odooinstaller import operations
from odooinstaller.registry import MARKER_FILE

from .conftest import RecordingRunner


@pytest.fixture
def configured(make_instance):
    inst = make_instance("shop")
    inst.instance_dir.mkdir(parents=True)
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
    assert "pw" not in " ".join(runner.commands[0])


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


def test_instances_sharing_source(registry, make_instance, layout):
    registry.add(make_instance("a", version="18.0"))
    registry.add(make_instance("b", version="18.0", http_port=8169, gevent_port=8172))
    registry.add(make_instance("c", version="19.0", http_port=8269, gevent_port=8272))
    registry.add(
        make_instance(
            "d",
            version="18.0",
            http_port=8369,
            gevent_port=8372,
            own_source=True,
            community_dir=layout.local_home / "d" / "src" / "community",
        )
    )
    shared = operations.instances_sharing_source(registry, registry.get("a"))
    assert [i.name for i in shared] == ["a", "b"]


def test_update_upgrades_every_instance_on_shared_code(registry, make_instance, monkeypatch):
    monkeypatch.setattr(operations, "status", lambda inst: operations.STATUS_STOPPED)
    monkeypatch.setattr(operations, "find_uv", lambda: "uv")
    dbs = {"a": ["a_main", "a_test"], "b": ["b_main"]}
    monkeypatch.setattr(operations, "list_databases", lambda runner, inst: dbs[inst.name])
    registry.add(make_instance("a"))
    registry.add(make_instance("b", http_port=8169, gevent_port=8172))
    runner = RecordingRunner()
    affected = operations.update(runner, registry, registry.get("a"), github_token=None)
    cmds = runner.joined()
    assert [i.name for i in affected] == ["a", "b"]
    assert sum("fetch --progress --depth 1 origin 18.0" in c for c in cmds) == 1
    assert any(c.endswith("gc --prune=now --quiet") for c in cmds)
    upgraded = [c.split(" -d ")[1].split()[0] for c in cmds if "-u all --stop-after-init" in c]
    assert upgraded == ["a_main", "a_test", "b_main"]  # every database of every affected instance


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
