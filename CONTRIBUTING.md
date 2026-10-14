# Contributing to odoobuddy

Thanks for helping! Bug reports, ideas and pull requests are all welcome.

## Reporting a problem

Open an [issue](https://github.com/niyasraphy/odoobuddy/issues/new/choose) and include
`odoobuddy --version`, your OS, the Odoo version and the error. Every run prints the path of
its full log file at the start; attach the relevant part, with **passwords and tokens removed**.

## Development setup

```bash
git clone https://github.com/niyasraphy/odoobuddy.git
cd odoobuddy
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest && ruff check . && ruff format --check . && mypy
```

Try your changes safely:

- `odoobuddy install --dry-run` prints every command without changing anything.
- `ODOOBUDDY_ROOT=/tmp/sandbox odoobuddy ...` keeps all files and the instance list inside
  `/tmp/sandbox` instead of your real ones.

The demo GIF (`docs/assets/demo.gif`) is recorded from a real, sandboxed session:
`pip install pexpect`, put [agg](https://github.com/asciinema/agg) on PATH, then run
`python scripts/record_demo.py`.

Preview the docs site with `pip install -e '.[docs]' && mkdocs serve`.

## How the code is organised

| Module | What it does |
|---|---|
| `cli.py` | Commands and terminal output |
| `wizard.py` | The install questions (every answer is also a flag) |
| `installer.py` | The install steps |
| `operations.py` | start / stop / run / update / backup / restore / remove / doctor |
| `editors.py`, `ide.py` | Editor projects, opening editors, finding installed editors |
| `repos.py`, `sources.py` | Custom repositories, Odoo source code, Enterprise access |
| `pull.py` | Copying a server database to a local instance |
| `render.py` | Text of every generated file (odoo.conf, Nginx, systemd, editor files) |
| `registry.py`, `layout.py` | The instance list and where files live |

## Guidelines

- Commands that change the system go through `Runner`, so `--dry-run` and logging work.
- Never put secrets on a command line: pass them on stdin or in environment variables.
- Add a test for every behaviour change; tests must not touch the real system
  (see `RecordingRunner` in `tests/conftest.py`).
- Keep messages short and in plain English: many users are not native speakers.
- Update `CHANGELOG.md` under the next version.
