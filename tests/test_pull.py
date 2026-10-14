import io
import subprocess
import tarfile
from datetime import date

import pytest

from odoobuddy import pull

from .conftest import RecordingRunner


def test_parse_source():
    assert pull.parse_source("root@erp.example.com:shop") == pull.Source("root@erp.example.com", "shop")
    assert pull.parse_source("deploy@10.0.0.5") == pull.Source("deploy@10.0.0.5", None)
    with pytest.raises(pull.PullError):
        pull.parse_source(":shop")


def test_names_and_sizes():
    assert pull.default_local_db("shop", date(2026, 10, 14)) == "shop_20261014"
    with pytest.raises(pull.PullError):
        pull.check_db_name("bad name;")
    assert pull.human_size(500) == "500 B" and pull.human_size(3 * 1024 * 1024) == "3.0 MB"


def test_ssh_commands_never_prompt():
    remote = pull.Remote("deploy@host", ssh_port=2222)
    cmd = remote.ssh(remote.as_postgres(["pg_dump", "my db"]))
    assert cmd[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
    assert cmd[5:7] == ["-p", "2222"] and cmd[-1] == "sudo -n -u postgres pg_dump 'my db'"
    remote.is_root = True
    assert remote.as_postgres(["psql"]) == ["runuser", "-u", "postgres", "--", "psql"]
    assert remote.as_root(["tar"]) == ["tar"]


class FakeRemote(pull.Remote):
    """Answers like a server with one odoobuddy instance 'shop' (Odoo 18.0)."""

    def __init__(
        self, *, root=True, ssh_ok=True, sudo_ok=True, registry=True, databases="shop\n", version="18.0"
    ):
        super().__init__("root@srv")
        self.answers = dict(
            root=root, ssh_ok=ssh_ok, sudo_ok=sudo_ok, registry=registry, databases=databases, version=version
        )
        self.streamed = []

    def run(self, remote):
        a, cmd = self.answers, " ".join(remote)

        def ok(out=""):
            return subprocess.CompletedProcess(remote, 0, out, "")

        def fail(err="boom"):
            return subprocess.CompletedProcess(remote, 1, "", err)

        if cmd == "id -u":
            return (
                ok("0\n" if a["root"] else "1000\n") if a["ssh_ok"] else fail("Permission denied (publickey)")
            )
        if cmd == "sudo -n true":
            return ok() if a["sudo_ok"] else fail("a password is required")
        if cmd.startswith("cat "):
            import json

            info = {"version": a["version"], "db_user": "odoo_shop", "data_dir": "/var/lib/odoo/shop"}
            registry = json.dumps({"instances": {"shop": info}})
            return ok(registry) if a["registry"] else fail()
        if "psql" in cmd:
            return ok(a["databases"] + "odoo_shop_install_check\n")
        if "test -d" in cmd:
            return ok()
        raise AssertionError(cmd)

    def stream(self, remote, sink, progress):
        self.streamed.append(remote)
        if "pg_dump" in remote:
            sink.write(b"PGDMP fake dump")
        else:  # tar of filestore/<db>
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                data = b"attachment"
                info = tarfile.TarInfo("shop/ab/abcdef")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            sink.write(buf.getvalue())
        progress(1234)


def test_connect_errors():
    with pytest.raises(pull.PullError, match="SSH keys"):
        FakeRemote(ssh_ok=False).connect()
    with pytest.raises(pull.PullError, match="password-less sudo"):
        FakeRemote(root=False, sudo_ok=False).connect()
    remote = FakeRemote(root=False)
    remote.connect()
    assert remote.is_root is False


def _plan(remote, inst, **kw):
    params = dict(db=None, local_db=None, remote_filestore=None, choose_db=lambda dbs: dbs[-1])
    params.update(kw)
    return pull.plan_pull(remote, pull.parse_source("root@srv:shop"), inst, **params)


def test_plan_reads_the_server(make_instance):
    inst = make_instance("dev", version="18.0")
    remote = FakeRemote()
    remote.connect()
    plan = _plan(remote, inst)
    assert plan.remote_db == "shop" and plan.local_db.startswith("shop_")
    assert plan.remote_filestore == "/var/lib/odoo/shop/filestore"
    picked = _plan(FakeRemote(databases="a\nb\n"), inst)
    assert picked.remote_db == "b"  # several databases: the user chooses


def test_plan_refuses_other_versions_and_unknown_instances(make_instance):
    with pytest.raises(pull.PullError, match="same Odoo version"):
        _plan(FakeRemote(version="19.0"), make_instance("dev", version="18.0"))
    with pytest.raises(pull.PullError, match="--db and --remote-filestore"):
        _plan(FakeRemote(registry=False), make_instance("dev"))
    remote = FakeRemote()
    with pytest.raises(pull.PullError, match="no instance 'nope'"):
        pull.plan_pull(
            remote,
            pull.parse_source("root@srv:nope"),
            make_instance("dev"),
            db=None,
            local_db=None,
            remote_filestore=None,
            choose_db=lambda d: d[0],
        )


def test_generic_server_needs_db(make_instance):
    with pytest.raises(pull.PullError, match="--db"):
        pull.plan_pull(
            FakeRemote(),
            pull.parse_source("root@srv"),
            make_instance("dev"),
            db=None,
            local_db=None,
            remote_filestore=None,
            choose_db=lambda d: d[0],
        )


def test_execute_pull_restores_files_and_neutralizes(make_instance):
    inst = make_instance("dev", version="18.0")
    inst.config_path.parent.mkdir(parents=True)
    inst.config_path.write_text("[options]\ndb_user = odoo_dev\ndb_password = pw\n")
    remote = FakeRemote()
    remote.connect()
    plan = _plan(remote, inst, local_db="shop_copy")
    runner = RecordingRunner()
    steps = []
    result = pull.execute_pull(
        runner,
        remote,
        inst,
        plan,
        neutralize=True,
        copy_filestore=True,
        step=lambda title, action: (steps.append(title), action()),
    )
    cmds = runner.joined()
    assert cmds[0] == "dropdb --if-exists --force shop_copy"
    assert cmds[1] == "createdb --owner odoo_dev shop_copy"
    assert cmds[2].startswith("pg_restore --no-owner --no-privileges --no-comments -d shop_copy")
    assert cmds[-1].endswith("neutralize -c " + str(inst.config_path) + " -d shop_copy")
    assert (inst.data_dir / "filestore" / "shop_copy" / "ab" / "abcdef").read_bytes() == b"attachment"
    assert runner.envs[1]["PGPASSWORD"] == "pw"
    assert result.neutralized and result.dump_size > 0 and result.filestore_size > 0
    assert [s.split()[0] for s in steps] == ["Copy", "Copy", "Restore", "Neutralize"]
    assert remote.streamed[0][:2] == ["runuser", "-u"]  # pg_dump as postgres on the server


def test_execute_pull_without_neutralize_or_files(make_instance):
    inst = make_instance("dev")
    inst.config_path.parent.mkdir(parents=True)
    inst.config_path.write_text("[options]\ndb_user = odoo_dev\ndb_password = pw\n")
    remote = FakeRemote()
    plan = _plan(remote, inst, local_db="x")
    runner = RecordingRunner()
    result = pull.execute_pull(
        runner,
        remote,
        inst,
        plan,
        neutralize=False,
        copy_filestore=False,
        step=lambda title, action: action(),
    )
    assert not result.neutralized and result.filestore_size == 0
    assert not any("neutralize" in c for c in runner.joined())
    assert len(remote.streamed) == 1


def test_safe_extract_blocks_escaping_paths(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("../evil")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    buf.seek(0)
    with tarfile.open(fileobj=buf) as tar, pytest.raises((pull.PullError, tarfile.TarError)):
        pull._safe_extract(tar, tmp_path / "out")
    assert not (tmp_path / "evil").exists()


def test_instance_first_syntax_for_pull():
    from odoobuddy.cli import rewrite_args

    assert rewrite_args(["dev", "pull", "root@srv:shop"], {"dev"}) == [
        "pull",
        "root@srv:shop",
        "--into",
        "dev",
    ]
