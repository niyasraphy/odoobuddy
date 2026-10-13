import json

import pytest

from odooinstaller import installer as installer_mod
from odooinstaller.installer import Installer
from odooinstaller.models import InstallSpec
from odooinstaller.registry import MARKER_FILE

from .conftest import RecordingRunner


@pytest.fixture(autouse=True)
def _no_host_lookups(monkeypatch):
    monkeypatch.setattr(installer_mod, "command_exists", lambda name: False)
    monkeypatch.setattr(installer_mod, "os_release", lambda: {"VERSION_CODENAME": "noble"})
    monkeypatch.setattr(installer_mod, "find_uv", lambda: "uv")


def run_all(inst: Installer) -> None:
    for step in inst.plan():
        step.action()


def titles(inst: Installer) -> list[str]:
    return [s.title for s in inst.plan()]


def test_local_install_writes_files_and_registers(layout, registry):
    spec = InstallSpec(
        name="shop", version="18.0", mode="local", ide="vscode", http_port=8169, gevent_port=8172
    )
    runner = RecordingRunner()
    inst = Installer(spec, layout=layout, registry=registry, runner=runner)
    run_all(inst)

    instance = registry.get("shop")
    assert instance is not None and registry.current() == "shop"
    folder = layout.local_home / "shop"
    conf = (folder / "conf" / "odoo.conf").read_text()
    assert "http_port = 8169" in conf and f"db_password = {inst.credentials.db_password}" in conf
    assert (folder / "conf" / "odoo.conf").stat().st_mode & 0o777 == 0o600
    assert (folder / "custom" / "addons").is_dir()
    assert (folder / MARKER_FILE).read_text().strip() == "shop"
    assert json.loads((folder / ".vscode" / "launch.json").read_text())
    assert (folder / "start.sh").stat().st_mode & 0o100

    cmds = runner.joined()
    assert any("git clone" in c and "--branch 18.0" in c and "shop/odoo/odoo" in c for c in cmds)
    assert any("uv venv --seed --python 3.12" in c for c in cmds)
    assert any(c.startswith("sudo -u postgres") and "psql" in c for c in cmds)
    # the setup is proven on a throw-away database, which is then deleted
    check = next(c for c in cmds if "-i base,contacts --stop-after-init" in c)
    assert "-d odoo_shop_install_check" in check and check.endswith("--without-demo=all")
    drops = [c for c in cmds if c.startswith("dropdb --if-exists --force odoo_shop_install_check")]
    assert len(drops) == 2  # leftover cleanup before, and removal after
    assert cmds.index(check) < len(cmds) - 1 - cmds[::-1].index(drops[-1])
    # passwords travel on stdin, never on the command line
    assert not any(inst.credentials.db_password in c for c in cmds)
    assert any(i and inst.credentials.db_password in i for i in runner.inputs)
    assert f"admin_passwd = {inst.credentials.master_password}" in conf


def test_local_plan_has_no_server_steps(layout, registry):
    spec = InstallSpec(name="dev", version="19.0", mode="local", ide="none")
    t = titles(Installer(spec, layout=layout, registry=registry, runner=RecordingRunner()))
    assert not any(word in " ".join(t) for word in ("Nginx", "SSL", "service", "firewall", "IDE", "VS Code"))


def test_enterprise_clone_uses_token_env_not_url(layout, registry):
    spec = InstallSpec(
        name="ee", version="19.0", mode="local", edition="enterprise", github_token="ghp_secret"
    )
    runner = RecordingRunner()
    inst = Installer(spec, layout=layout, registry=registry, runner=runner)
    inst.download_enterprise()
    (cmd,), (env,) = runner.commands, runner.envs
    assert "https://github.com/odoo/enterprise.git" in cmd
    assert "ghp_secret" not in " ".join(cmd)
    assert "GIT_CONFIG_VALUE_0" in env
    assert inst.instance.enterprise_dir is not None


def test_server_install(layout, registry, monkeypatch):
    spec = InstallSpec(
        name="prod",
        version="19.0",
        mode="server",
        domain="erp.example.com",
        ssl=True,
        email="me@example.com",
        workers=4,
        firewall=True,
    )
    runner = RecordingRunner(root=True)
    inst = Installer(spec, layout=layout, registry=registry, runner=runner)
    assert titles(inst)[-6:] == [
        "Create service odoo-prod",
        "Configure Nginx for erp.example.com",
        "Get SSL certificate (Let's Encrypt)",
        "Configure firewall",
        "Configure log rotation",
        "Register instance",
    ]
    run_all(inst)
    cmds = runner.joined()
    assert any(c.startswith("useradd --system") and c.endswith("odoo-prod") for c in cmds)
    assert any(c.startswith("runuser -u odoo-prod --") and "-i base" in c for c in cmds)
    assert "systemctl enable --now odoo-prod" in cmds
    assert any(c.startswith("certbot --nginx -d erp.example.com") and "-m me@example.com" in c for c in cmds)
    assert "ufw allow Nginx Full" in cmds and "ufw --force enable" in cmds
    assert (layout.systemd_dir / "odoo-prod.service").exists()
    assert (layout.nginx_dir / "sites-available" / "odoo-prod.conf").exists()
    assert (layout.logrotate_dir / "odoo-prod").exists()
    conf = (layout.server_root / "instances" / "prod" / "conf" / "odoo.conf").read_text()
    assert "workers = 4" in conf and "proxy_mode = True" in conf
    assert registry.get("prod").url == "https://erp.example.com"


def test_ssl_failure_does_not_fail_install(layout, registry):
    spec = InstallSpec(name="prod", version="19.0", mode="server", domain="erp.example.com", ssl=True)
    runner = RecordingRunner(root=True, fail_on="certbot")
    inst = Installer(spec, layout=layout, registry=registry, runner=runner)
    run_all(inst)
    assert inst.warnings and "certbot --nginx" in inst.warnings[0]
    assert registry.get("prod").url == "http://erp.example.com"


def test_server_without_domain_opens_port(layout, registry):
    spec = InstallSpec(name="prod", version="18.0", mode="server", http_port=8069, firewall=True)
    runner = RecordingRunner(root=True)
    inst = Installer(spec, layout=layout, registry=registry, runner=runner)
    assert not any("Nginx" in t or "SSL" in t for t in titles(inst))
    inst.configure_firewall()
    assert "ufw allow 8069/tcp" in runner.joined()


def test_clone_is_shallow_single_branch(layout, registry):
    spec = InstallSpec(name="shop", version="20.0", mode="local")
    runner = RecordingRunner()
    Installer(spec, layout=layout, registry=registry, runner=runner).download_community()
    clone = runner.commands[-1]
    assert clone[:2] == ["git", "clone"]
    assert clone[clone.index("--depth") + 1] == "1"
    assert "--single-branch" in clone and clone[clone.index("--branch") + 1] == "20.0"


def test_rerun_skips_complete_clone(layout, registry, monkeypatch):
    from odooinstaller import sources

    spec = InstallSpec(name="shop", version="18.0", mode="local")
    runner = RecordingRunner()
    inst = Installer(spec, layout=layout, registry=registry, runner=runner)
    (inst.instance.community_dir / ".git").mkdir(parents=True)
    monkeypatch.setattr(sources, "is_complete_clone", lambda r, d, b: True)
    inst.download_community()
    assert runner.commands == []


def test_interrupted_clone_is_downloaded_again(layout, registry):
    spec = InstallSpec(name="shop", version="18.0", mode="local")
    runner = RecordingRunner()  # rev-parse prints nothing -> not a usable checkout
    inst = Installer(spec, layout=layout, registry=registry, runner=runner)
    leftover = inst.instance.community_dir / ".git" / "half-downloaded.pack"
    leftover.parent.mkdir(parents=True)
    leftover.write_text("x")
    inst.download_community()
    assert not leftover.exists()
    assert runner.commands[-1][:2] == ["git", "clone"]


def test_failed_check_still_drops_test_database(layout, registry):
    spec = InstallSpec(name="shop", version="19.0", mode="local")
    runner = RecordingRunner(fail_on=str(layout.local_home / "shop" / "venv" / "bin" / "python"))
    inst = Installer(spec, layout=layout, registry=registry, runner=runner)
    inst.write_config()
    from odooinstaller.system import CommandError

    with pytest.raises(CommandError):
        inst.test_installation()
    assert runner.joined()[-1].startswith("dropdb --if-exists --force odoo_shop_install_check")


def test_pycharm_project_files(layout, registry):
    spec = InstallSpec(name="shop", version="20.0", mode="local", ide="pycharm")
    inst = Installer(spec, layout=layout, registry=registry, runner=RecordingRunner())
    inst.configure_ide()
    idea = layout.local_home / "shop" / ".idea"
    iml = (idea / "shop.iml").read_text()
    assert str(inst.instance.community_dir) in iml and "venv" in iml
    assert 'project-jdk-name="Python 3.12 (shop)"' in (idea / "misc.xml").read_text()
    (idea / "misc.xml").write_text("user changed")
    inst.configure_ide()
    assert (idea / "misc.xml").read_text() == "user changed"  # never overwritten


def test_layout_keeps_everything_in_the_instance_folder(layout, registry):
    spec = InstallSpec(name="t20", version="20.0", mode="local", edition="enterprise", github_token="x")
    inst = Installer(spec, layout=layout, registry=registry, runner=RecordingRunner()).instance
    base = layout.local_home / "t20"
    assert inst.community_dir == base / "odoo" / "odoo"
    assert inst.enterprise_dir == base / "odoo" / "enterprise"
    assert inst.custom_dir == base / "custom"
    assert inst.config_path == base / "conf" / "odoo.conf"


def test_custom_repos_are_cloned_and_their_requirements_installed(layout, registry):
    from odooinstaller.models import RepoSpec

    spec = InstallSpec(
        name="shop",
        version="18.0",
        mode="local",
        repos=[RepoSpec("https://github.com/OCA/web.git", "18.0", token="tok")],
    )
    runner = RecordingRunner()
    inst = Installer(spec, layout=layout, registry=registry, runner=runner)
    assert "Clone 1 custom repository" in titles(inst)
    inst.clone_repos()
    (cmd,), (env,) = runner.commands, runner.envs
    assert cmd[:2] == ["git", "clone"] and cmd[-1].endswith("shop/custom/web")
    assert "tok" not in " ".join(cmd) and "GIT_CONFIG_VALUE_0" in env
    (inst.instance.custom_dir / "web" / "requirements.txt").parent.mkdir(parents=True, exist_ok=True)
    (inst.instance.custom_dir / "web" / "requirements.txt").write_text("x\n")
    runner.commands.clear()
    inst.create_python_env()
    assert any(
        str(inst.instance.custom_dir / "web" / "requirements.txt") in " ".join(c) for c in runner.commands
    )
