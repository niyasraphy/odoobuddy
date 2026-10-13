# Changelog

## [0.1.0] - unreleased

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
