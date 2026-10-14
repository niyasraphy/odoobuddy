# Changelog

## [0.1.1] - unreleased

### Fixed
- Server without a domain: Nginx now serves the public port and routes `/websocket`, so
  live chat and notifications work with multiple workers (Odoo uses internal ports).
- Server install: the test installation prints its output instead of writing only to the
  log file, and the installer checks that the service really answers after starting.
- A running local instance is detected by its config file when the pid file is missing.
- Odoo 20: install `markdown2`, which Odoo uses but does not list in its requirements.
- Odoo crashed after reloading itself (dev-mode reload, module installs) when its virtual
  environment was not activated: it restarted with the system Python. Fixed for every launch
  method, including systemd on servers.

### Added
- Server wizard: when the domain does not point to the server yet, shows the DNS record to add and offers to check again, continue without a domain, or keep it and add SSL later
- "How do you want to run Odoo?": in an editor, in a terminal (`odoobuddy run`) or in
  the background. Editors open automatically after the install and Odoo starts inside
  them (VS Code family and PyCharm automatically, Zed with one command).
- "Attach to Odoo": attach a debugger to the running Odoo at any time, also after it
  reloads itself; each instance has its own debug port.
- Editors: Cursor, Windsurf, VSCodium and Zed, detected automatically.
- New commands: `run` (foreground in this terminal) and `open` (open or switch editor).
- `pull`: copy a production database and filestore over SSH into a local instance,
  neutralized with Odoo's own `neutralize` (works with non-odoobuddy servers too).
- Tab completion for commands and instance names (`odoobuddy --install-completion`).
- The summary menu (install / change a setting / cancel) also appears in dry runs, and
  dry runs show paths under your home folder as `~/...`.
- Documentation site, demo GIF, issue and pull request templates, CONTRIBUTING and SECURITY.

### Changed
- Installing from a script (no terminal) now requires `--yes`.

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
