"""Which Odoo versions exist, and which Python each one needs."""

from __future__ import annotations

import re
import subprocess

ODOO_REPO = "https://github.com/odoo/odoo.git"
ENTERPRISE_REPO = "https://github.com/odoo/enterprise.git"
MIN_MAJOR = 16
FALLBACK_VERSIONS = ["19.0", "18.0", "17.0", "16.0"]

# Python versions Odoo officially supports, picking a well-tested one per release.
_PYTHON_FOR = {16: "3.10", 17: "3.11", 18: "3.12", 19: "3.12"}
_DEFAULT_PYTHON = "3.12"

_BRANCH_RE = re.compile(r"refs/heads/(\d+)\.0$")


def major(version: str) -> int:
    return int(version.split(".", 1)[0])


def python_for(version: str) -> str:
    return _PYTHON_FOR.get(major(version), _DEFAULT_PYTHON)


def parse_branches(ls_remote_output: str) -> list[str]:
    majors = {
        int(m.group(1)) for line in ls_remote_output.splitlines() if (m := _BRANCH_RE.search(line.strip()))
    }
    return [f"{v}.0" for v in sorted(majors, reverse=True) if v >= MIN_MAJOR]


def available_versions(timeout: float = 20) -> list[str]:
    """Read stable branches from Odoo's GitHub, so new releases appear automatically."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", ODOO_REPO],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
            env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
    except (OSError, subprocess.SubprocessError):
        return list(FALLBACK_VERSIONS)
    return parse_branches(result.stdout) or list(FALLBACK_VERSIONS)


def no_demo_flags(version: str) -> list[str]:
    """Odoo 19 stopped loading demo data by default (and replaced --without-demo)."""
    return [] if major(version) >= 19 else ["--without-demo=all"]
