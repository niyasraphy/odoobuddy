# Changelog

## [0.1.0] - unreleased

### Added
- Interactive install wizard for local and server mode, with a flag for every question.
- Odoo versions read live from GitHub, and the matching Python installed via uv.
- Enterprise support with a GitHub token check and fallback to Community.
- Multiple instances per machine, with automatic port allocation and shared source code per version.
- Server mode: systemd, Nginx (websocket), Let's Encrypt, ufw, log rotation, tuned workers.
- VS Code and PyCharm debug configurations for local instances.
- Commands: list, info, use, start, stop, restart, logs, shell, update, backup, restore, remove, doctor, versions.
