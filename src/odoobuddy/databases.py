"""Create Odoo databases without the database manager, so production can keep it closed."""

from __future__ import annotations

import json
import shutil
from typing import Literal

from . import postgres
from .models import Instance
from .system import CommandError, Runner

READY_MARK = "odoobuddy.database_ready"
LANGUAGE = "en_US"

State = Literal["missing", "ready", "unfinished"]

# Runs inside the instance's Python. Everything secret arrives on stdin, never on the command line.
# Odoo logs a failed setup instead of raising it, and it writes the country and the admin password
# in one commit: so finding the country proves the password was set too. Without this check, a
# failure could leave a working database whose admin password is still "admin".
CREATE_SCRIPT = f"""\
import json, sys
args = json.load(sys.stdin)
sys.path.insert(0, args["source"])
import odoo
from odoo.sql_db import db_connect
from odoo.tools import config
config.parse_config(["-c", args["config"]])
config["list_db"] = True  # for this process only: Odoo refuses to create databases otherwise
new = dict(
    demo=False, lang={LANGUAGE!r}, user_password=args["password"], country_code=args["country"], phone=None
)
try:
    from odoo.modules.db import create
except ImportError:
    from odoo.service.db import exp_create_database
    exp_create_database(db_name=args["name"], login="admin", **new)
else:
    create(db_name=args["name"], user_login="admin", **new)
with db_connect(args["name"]).cursor() as cr:
    cr.execute(
        "SELECT c.code FROM res_company co JOIN res_partner p ON p.id = co.partner_id "
        "JOIN res_country c ON c.id = p.country_id WHERE co.id = 1"
    )
    row = cr.fetchone()
    if not row or row[0] != args["country"]:
        sys.exit("Odoo did not finish setting up the database: see the output above")
    cr.execute("INSERT INTO ir_config_parameter (key, value) VALUES (%s, 'true')", ({READY_MARK!r},))
    cr.commit()
"""


def _psql(runner: Runner, instance: Instance, db: str, sql: str) -> str:
    env = postgres.client_env(postgres.read_db_settings(instance.config_path))
    result = runner.run(["psql", "-X", "-tA", "-d", db, "-c", sql], env=env, check=False)
    return (result.stdout or "").strip()


def state(runner: Runner, instance: Instance, name: str) -> State:
    """Whether the database exists, and if so whether odoobuddy finished setting it up."""
    if runner.dry_run:
        return "missing"
    if _psql(runner, instance, "postgres", f"SELECT 1 FROM pg_database WHERE datname = '{name}'") != "1":
        return "missing"
    mark = f"SELECT 1 FROM ir_config_parameter WHERE key = '{READY_MARK}'"
    return "ready" if _psql(runner, instance, name, mark) == "1" else "unfinished"


def create(runner: Runner, instance: Instance, name: str, country: str, password: str) -> None:
    """Create and set up database ``name``, which must not exist yet. Removes it again on any failure."""
    if runner.dry_run:
        runner.console.print(f"  [dim]create database {name} (country {country})[/dim]")
        return
    answers = {
        "source": str(instance.community_dir),
        "config": str(instance.config_path),
        "name": name,
        "country": country,
        "password": password,
    }
    command = [str(instance.python_bin), "-c", CREATE_SCRIPT]
    as_user = instance.system_user if instance.mode == "server" else None
    try:
        runner.run(command, as_user=as_user, input=json.dumps(answers), cwd=instance.instance_dir)
    except BaseException:  # also Ctrl+C: never leave a half-made database with the default password
        _remove(runner, instance, name)
        raise


def _remove(runner: Runner, instance: Instance, name: str) -> None:
    env = postgres.client_env(postgres.read_db_settings(instance.config_path))
    runner.run(["dropdb", "--if-exists", "--force", name], env=env, check=False)
    shutil.rmtree(instance.data_dir / "filestore" / name, ignore_errors=True)


def unfinished_error(name: str) -> CommandError:
    return CommandError(
        ["createdb", name],
        1,
        f"Database {name} already exists, but odoobuddy did not finish setting it up, so its admin "
        f"password may still be the default.\nIf it holds nothing you need, remove it with: "
        f"sudo -u postgres dropdb --force {name}\nOtherwise, choose another database name.",
    )
