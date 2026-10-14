# Changelog

## [0.1.1] - 2026-10-14

### Fixed
- Server without a domain: Nginx now serves the public port and routes `/websocket`, so
  live chat and notifications work with multiple workers (Odoo uses internal ports).
- Server install: the test installation prints its output instead of writing only to the
  log file, and the installer checks that the service really answers after starting.
- A running local instance is detected by its config file when the pid file is missing.
- Odoo 20: install `markdown2`, which Odoo uses but does not list in its requirements.

### Added
- Cursor, Windsurf and VSCodium support (same F5 debugging as VS Code), detected
  automatically like VS Code and PyCharm.

## [0.1.0] - 2026-10-13

### Added
- Interactive install wizard for local and server mode, with a flag for every question.
- Odoo versions read live from GitHub, and the matching Python installed via uv.
- Enterprise support with a GitHub token check and fallback to Community.
- Multiple instances per machine, with automatic port allocation.
- Server mode: systemd, Nginx (websocket), Let's Encrypt, ufw, log rotation, tuned workers.
- VS Code and PyCharm debug configurations for local instances.
- No database is created for you: the install is verified by installing `contacts` in a
  temporary database, which is then deleted; create your own in the database manager.
- Installed IDEs (VS Code, PyCharm) are detected and pre-selected; PyCharm gets a full project.
- Each instance is self-contained: odoo/odoo, odoo/enterprise, custom/, conf/ in one folder.
- Custom repositories: cloned during install or later with `odoobuddy NAME clone URL [BRANCH]`,
  with automatic addons_path and requirements; `repos` lists them, `update` pulls them.
- `odoobuddy NAME COMMAND` works as well as `odoobuddy COMMAND NAME`.
- Commands: list, info, use, start, stop, restart, logs, shell, update, backup, restore, remove, doctor, versions.
