import pytest

from odooinstaller.registry import MARKER_FILE, RegistryError, instance_from_cwd, resolve


def test_add_get_remove_and_current(registry, make_instance):
    shop = make_instance("shop")
    prod = make_instance("prod", mode="server")
    registry.add(shop)
    registry.add(prod)
    assert set(registry.all()) == {"shop", "prod"}
    assert registry.get("prod").mode == "server"
    assert registry.layout.registry_path("server").stat().st_mode & 0o777 == 0o644
    assert registry.layout.registry_path("local").stat().st_mode & 0o777 == 0o600

    registry.set_current("shop")
    assert registry.current() == "shop"
    registry.remove("shop")
    assert registry.get("shop") is None
    assert registry.current() is None


def test_names_are_unique_across_modes(registry, make_instance):
    registry.add(make_instance("shop"))
    with pytest.raises(RegistryError, match="already exists"):
        registry.add(make_instance("shop", mode="server"))


def test_corrupt_registry_is_reported(registry):
    path = registry.layout.registry_path("local")
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    with pytest.raises(RegistryError, match="cannot read"):
        registry.all()


def test_instance_from_cwd(tmp_path):
    folder = tmp_path / "shop" / "addons" / "my_module"
    folder.mkdir(parents=True)
    (tmp_path / "shop" / MARKER_FILE).write_text("shop\n")
    assert instance_from_cwd(folder) == "shop"
    assert instance_from_cwd(tmp_path) is None


@pytest.fixture
def three(registry, make_instance):
    for name, port in (("shop", 8069), ("hr", 8169), ("crm", 8269)):
        registry.add(make_instance(name, http_port=port, gevent_port=port + 3))
    return registry


def test_resolve_priority(three, tmp_path):
    folder = tmp_path / "work" / "hr"
    folder.mkdir(parents=True)
    (folder / MARKER_FILE).write_text("hr")
    three.set_current("crm")

    def never(_):
        raise AssertionError("should not ask")

    # 1. explicit name wins over everything
    assert resolve(three, "shop", cwd=folder, choose=never).name == "shop"
    # 2. then the folder we are in
    assert resolve(three, None, cwd=folder, choose=never).name == "hr"
    # 3. then the `use` choice
    assert resolve(three, None, cwd=tmp_path, choose=never).name == "crm"
    # 4. otherwise ask
    three.set_current(None)
    picked = resolve(three, None, cwd=tmp_path, choose=lambda items: items[-1])
    assert picked.name == "shop"  # sorted: crm, hr, shop


def test_resolve_errors(three, registry, tmp_path, layout):
    with pytest.raises(RegistryError, match="Known instances: crm, hr, shop"):
        resolve(three, "nope", cwd=tmp_path, choose=None)
    with pytest.raises(RegistryError, match="which instance"):
        resolve(three, None, cwd=tmp_path, choose=None)


def test_resolve_with_no_instances(registry, tmp_path):
    with pytest.raises(RegistryError, match="no instances yet"):
        resolve(registry, None, cwd=tmp_path, choose=None)


def test_stale_current_is_ignored(three, tmp_path):
    three.set_current("deleted-one")
    assert resolve(three, None, cwd=tmp_path, choose=lambda items: items[0]).name == "crm"
