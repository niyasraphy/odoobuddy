import pytest

from odoobuddy import repos, sources
from odoobuddy.cli import rewrite_args
from odoobuddy.models import RepoSpec

from .conftest import RecordingRunner
from .test_wizard import Scripted, make_ctx


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://github.com/OCA/web.git", "web"),
        ("https://gitlab.com/acme/my-mods/", "my-mods"),
        ("git@github.com:acme/odoo-custom.git", "odoo-custom"),
    ],
)
def test_repo_dir_name(url, name):
    assert repos.repo_dir_name(url) == name


def test_repo_dir_name_rejects_unusable():
    with pytest.raises(ValueError):
        repos.repo_dir_name("https://example.com/addons")


def _module(path):
    path.mkdir(parents=True)
    (path / "__manifest__.py").write_text("{}")


def test_module_folders_handles_common_layouts(tmp_path):
    oca = tmp_path / "web"
    _module(oca / "web_responsive")
    _module(oca / "web_dialog_size")
    _module(oca / "setup" / "web_responsive" / "odoo" / "addons" / "web_responsive")  # OCA symlink farm
    nested = tmp_path / "company"
    _module(nested / "addons" / "sale_custom")
    _module(nested / "addons" / "sale_custom" / "static" / "fake" / "x")  # inside a module: ignored
    deep = tmp_path / "deep"
    _module(deep / "a" / "b" / "c" / "too_deep")

    assert repos.module_folders(oca) == [oca]
    assert repos.module_folders(nested) == [nested / "addons"]
    assert repos.module_folders(deep) == []


def test_custom_addons_paths_order(tmp_path):
    custom = tmp_path / "custom"
    (custom / "addons").mkdir(parents=True)
    _module(custom / "zeta" / "mod_z")
    _module(custom / "alpha" / "src" / "mod_a")
    assert repos.custom_addons_paths(custom) == [custom / "addons", custom / "alpha" / "src", custom / "zeta"]


def test_refresh_addons_path_keeps_other_edits(make_instance):
    inst = make_instance("shop")
    inst.config_path.parent.mkdir(parents=True)
    inst.config_path.write_text("[options]\naddons_path = /old\nlimit_time_real = 9999  ; my edit\n")
    _module(inst.custom_dir / "web" / "web_responsive")
    paths = repos.refresh_addons_path(RecordingRunner(), inst)
    text = inst.config_path.read_text()
    assert "limit_time_real = 9999  ; my edit" in text and "/old" not in text
    assert str(inst.custom_dir / "web") in text and inst.custom_dir / "web" in paths


def test_clone_repo_refuses_existing_folder(make_instance):
    inst = make_instance("shop")
    (inst.custom_dir / "web").mkdir(parents=True)
    with pytest.raises(FileExistsError):
        repos.clone_repo(RecordingRunner(), inst, "https://github.com/OCA/web.git", "18.0")


class _Answer(RecordingRunner):
    def __init__(self, code, output):
        super().__init__()
        self.code, self.output = code, output

    def run(self, cmd, **kwargs):
        result = super().run(cmd, **kwargs)
        result.returncode, result.stdout = self.code, self.output
        return result


def test_check_repo_outcomes():
    ok = _Answer(0, "abc\trefs/heads/18.0\n")
    assert sources.check_repo(ok, "https://github.com/OCA/web.git", "18.0").ok
    missing_branch = sources.check_repo(_Answer(0, ""), "https://x/y.git", "99.0")
    assert not missing_branch.ok and "does not exist" in missing_branch.message
    private = sources.check_repo(
        _Answer(128, "remote: Repository not found.\nfatal: ..."), "https://x/y", "1"
    )
    assert private.needs_auth
    offline = sources.check_repo(
        _Answer(128, "fatal: unable to access: Could not resolve host"), "https://x", "1"
    )
    assert not offline.needs_auth and "Could not resolve host" in offline.message


def test_check_repo_sends_token_for_that_host_only():
    runner = _Answer(0, "abc\trefs/heads/1\n")
    sources.check_repo(runner, "https://gitlab.com/acme/x.git", "1", token="glpat")
    env = runner.envs[0]
    assert env["GIT_CONFIG_KEY_0"] == "http.https://gitlab.com/.extraheader"
    assert env["GIT_SSH_COMMAND"].startswith("ssh -o BatchMode=yes")


def test_wizard_collects_repos_and_asks_token_for_private_ones():
    checks = []

    def check(url, branch, token):
        checks.append((url, branch, token))
        if "private" in url:
            from odoobuddy.sources import AccessResult

            return AccessResult(token == "tok", "access denied", needs_auth=True)
        if "missing" in url:
            from odoobuddy.sources import AccessResult

            return AccessResult(False, "branch '18.0' does not exist")
        from odoobuddy.sources import AccessResult

        return AccessResult(True, "ok")

    p = Scripted(
        "development", "18.0", "community", "",
        True,
        "https://github.com/OCA/web.git", "",            # default branch = 18.0
        "https://github.com/acme/private.git", "17.0", "tok",
        "https://github.com/acme/missing.git", "",
        "",                                                # empty URL: done
        "background",
    )  # fmt: skip
    # Development asks about repositories only when they are chosen in the summary's "Change a setting".
    spec = run(p, make_ctx(check_repo=check, preset={"_editing": "repos"}))
    assert spec.repos == [
        RepoSpec("https://github.com/OCA/web.git", "18.0"),
        RepoSpec("https://github.com/acme/private.git", "17.0", token="tok"),
    ]
    assert any("skipped https://github.com/acme/missing.git" in s for s in p.said)


def run(p, ctx):
    from odoobuddy.wizard import run_wizard

    return run_wizard(p, ctx)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["testing20", "clone", "URL", "17.0"], ["clone", "URL", "17.0", "--instance", "testing20"]),
        (["testing20", "start"], ["start", "testing20"]),
        (["testing20", "logs", "-n", "5"], ["logs", "testing20", "-n", "5"]),
        (
            ["testing20", "restore", "b.zip", "--db", "x"],
            ["restore", "b.zip", "--db", "x", "--name", "testing20"],
        ),
        (["start", "testing20"], ["start", "testing20"]),  # normal order untouched
        (["unknown", "start"], ["unknown", "start"]),
        (["testing20"], ["testing20"]),
    ],
)
def test_instance_first_syntax(args, expected):
    assert rewrite_args(args, {"testing20"}) == expected
