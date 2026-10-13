"""PostgreSQL roles and databases. SQL is sent on stdin so passwords never show in ``ps``."""

from __future__ import annotations

import configparser
import re
from pathlib import Path

from .system import Runner

_ROLE_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def _check_role(role: str) -> str:
    if not _ROLE_RE.fullmatch(role):
        raise ValueError(f"invalid PostgreSQL role name: {role!r}")
    return role


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def psql_as_postgres(runner: Runner, sql: str, *, capture: bool = False) -> str:
    result = runner.run(
        ["psql", "-v", "ON_ERROR_STOP=1", "-X", "-q", "-tA"],
        as_user="postgres",
        input=sql,
        cwd="/",
    )
    return result.stdout if capture else ""


def ensure_role(runner: Runner, role: str, password: str) -> None:
    """Create the role, or reset its password if it exists (so re-runs stay consistent)."""
    role = _check_role(role)
    pw = _sql_literal(password)
    psql_as_postgres(
        runner,
        f"""DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
    CREATE ROLE "{role}" LOGIN CREATEDB PASSWORD {pw};
  ELSE
    ALTER ROLE "{role}" WITH LOGIN CREATEDB PASSWORD {pw};
  END IF;
END $$;
""",
    )


def drop_role_and_databases(runner: Runner, role: str) -> list[str]:
    """Drop every database owned by ``role``, then the role. Returns dropped database names."""
    role = _check_role(role)
    out = psql_as_postgres(
        runner,
        "SELECT d.datname FROM pg_database d JOIN pg_roles r ON d.datdba = r.oid "
        f"WHERE r.rolname = '{role}';",
        capture=True,
    )
    databases = [line.strip() for line in out.splitlines() if line.strip()]
    for db in databases:
        runner.run(["dropdb", "--if-exists", "--force", db], as_user="postgres", cwd="/")
    psql_as_postgres(runner, f'DROP ROLE IF EXISTS "{role}";')
    return databases


def read_db_settings(config_path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config_path, encoding="utf-8")
    if not parser.has_section("options"):
        raise FileNotFoundError(f"cannot read database settings from {config_path}")
    opts = parser["options"]
    return {
        "host": opts.get("db_host", "localhost"),
        "port": opts.get("db_port", "5432"),
        "user": opts.get("db_user", ""),
        "password": opts.get("db_password", ""),
    }


def client_env(settings: dict[str, str]) -> dict[str, str]:
    return {
        "PGHOST": settings["host"],
        "PGPORT": settings["port"],
        "PGUSER": settings["user"],
        "PGPASSWORD": settings["password"],
    }
