# Command reference

Every command that takes an instance accepts it in either order:
`odoobuddy start shop` or `odoobuddy shop start`. Without a name, odoobuddy uses the
instance folder you are in, then the one set with `use`, and otherwise asks.

Run `odoobuddy COMMAND --help` for all options.

## Instances

| Command | What it does |
|---|---|
| `odoobuddy` / `install` | Create an instance (wizard; every question is also a flag) |
| `list` | All instances with version, mode, port, URL and status |
| `info NAME` | Paths, ports, databases, editor steps |
| `use NAME` | Set the current instance |
| `remove NAME` | Delete the instance, its databases, files, service and web config |
| `doctor` | Check the system and every instance |
| `versions` | Odoo versions available to install |

## Running

| Command | What it does |
|---|---|
| `run NAME` | Run in this terminal with live logs (Ctrl+C stops it) |
| `open NAME [--with EDITOR]` | Open in its editor; Odoo starts inside it |
| `start / stop / restart NAME` | Background (local) or systemd service (server) |
| `logs NAME [-n 500] [--no-follow]` | Show the log |
| `shell NAME [--db DB]` | Odoo shell with `env` |

## Code and modules

| Command | What it does |
|---|---|
| `clone URL [BRANCH]` | Clone a custom repository into `custom/` and update `addons_path` |
| `repos NAME` | List custom repositories |
| `update NAME [--modules all]` | Pull Odoo and repositories, upgrade modules in every database |

## Databases

| Command | What it does |
|---|---|
| `backup NAME [--db DB] [--to DIR]` | Database + filestore → zip (Odoo's own format) |
| `restore FILE --db DB [--name NAME]` | Restore a zip into a database (created or replaced) |
| `pull USER@HOST:INSTANCE --into NAME` | Copy production to local, neutralized ([details](pull.md)) |

## Global

| Option | What it does |
|---|---|
| `--version` | Show the odoobuddy version |
| `--install-completion` | Tab completion for bash, zsh or fish (commands and instance names) |
| `--dry-run` | On install, update and remove: show every command, change nothing |
