# Command reference

Every command that takes an instance accepts it in either order:
`odoobuddy start shop` or `odoobuddy shop start`. Without a name, odoobuddy uses the
instance folder you are in, then the one set with `use`, and otherwise asks.

Run `odoobuddy COMMAND --help` for all options.

## Instances

| Command | What it does |
|---|---|
| `odoobuddy` | Welcome screen: your instances and the commands to start with |
| `install` | Create an instance (wizard; every question is also a flag, e.g. `--purpose test`) |
| `list` | All instances with version, purpose, port, URL and status |
| `status [NAME]` | Running or stopped, and the address |
| `info NAME` | Paths, ports, databases, editor steps |
| `use NAME` | Set the current instance |
| `remove NAME` | Delete the instance, its databases, files, service and web config |
| `doctor` | Check the system and every instance |
| `versions` | Odoo versions available to install |
| `support` | Documentation, bug reports and contact |
| `help [COMMAND]`, `-h` | Help for odoobuddy or one command |

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
| `backup NAME [--db DB \| --all] [--to DIR] [--keep N]` | Database + filestore → zip (Odoo's own format); `--keep` deletes older ones |
| `backup NAME --nightly` / `--no-nightly` | Server: back up every database each night, the last 7 kept |
| `restore FILE --db DB [--name NAME]` | Restore a zip into a database (created or replaced) |
| `pull USER@HOST:INSTANCE --into NAME` | Copy production to local, neutralized ([details](pull.md)) |

## Global

| Option | What it does |
|---|---|
| `--version` | Show the odoobuddy version |
| `--install-completion` | Tab completion for bash, zsh or fish (commands and instance names) |
| `--dry-run` | On install, update and remove: show every command, change nothing |
