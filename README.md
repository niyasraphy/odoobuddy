# odoobuddy

**Install and manage many Odoo instances, of any version, on your laptop or on production servers, with one command.**

```bash
pip install odoobuddy
odoobuddy
```

The wizard asks a few questions, then sets up everything:

```
? Where are you installing Odoo?   Local - my computer, for development
? Which Odoo version?              Odoo 19.0
? Which edition?                   Enterprise
? GitHub token with access to odoo/enterprise (input hidden): ****
  ✓ access to odoo/enterprise confirmed
? Instance name:                   shop
? Which port should Odoo run on?   8069
? Which IDE do you use?            PyCharm (ready run configuration) - ✓ installed

✓ [8/10] Test installation (contacts in a temporary database) (1m 12s)

╭──────────────── shop · Odoo 19.0 Enterprise ─────────────────╮
│ ✓ Odoo 19.0 is installed and working                         │
│                                                              │
│ Create your first database                                   │
│   1. Open http://localhost:8069/web/database/manager         │
│   2. Master password: x8Kf2...                               │
│   3. Choose name, language, country, demo data, admin login  │
╰──────────────────────────────────────────────────────────────╯
```

The installer **does not create your database**. It proves the setup works by installing
a real module (`contacts`) in a temporary database, deletes it, and lets you create your
own databases in Odoo's database manager, choosing the name, language, country, demo data
and admin password yourself.

> `odoobuddy` is a community project and is **not affiliated with Odoo S.A.**

## Features

- **Any Odoo version, read live from Odoo's GitHub.** 16.0 through 20.0 today, and new releases appear automatically.
- **Community or Enterprise.** Your GitHub token is checked *before* cloning. If it doesn't have access, the wizard offers to retry or continues with Community. The token is never saved to disk, git config or logs.
- **Many instances side by side.** Run Odoo 17, 18, 19 and 20 at the same time, or several instances of one version. Each instance gets its own Python environment, config, database user, ports, filestore and logs.
- **The right Python for each version**, installed automatically with [uv](https://github.com/astral-sh/uv). Odoo 16 gets 3.10 and Odoo 19 gets 3.12, with no system Python changes.
- **Local mode** for development: ready-to-debug **VS Code** (`F5`) or **PyCharm** projects, dev mode with auto-reload, and a `start.sh` script. Installed IDEs are detected and pre-selected. Your IDE is never installed or changed; only `.vscode/` or `.idea/` is written in the instance folder.
- **Server mode** for production:
  - a dedicated system user and a **systemd** service
  - **Nginx** with websocket support
  - free **Let's Encrypt SSL**, after checking that your domain points to the server
  - a **ufw firewall** that never locks out your SSH port
  - worker counts tuned to your CPU and RAM
  - log rotation
- **Always clear which Odoo you are touching.** Every command shows a banner, production instances are labelled in red, and destructive commands make you type the instance name.
- **Safe by default.** `--dry-run` everywhere, a backup before production updates, generated passwords, and SQL and passwords passed on stdin, never on the command line.

## Requirements

- Linux: **Ubuntu 22.04 / 24.04** or **Debian 12** (others may work, untested)
- Python 3.10+ to run `odoobuddy` itself
- `sudo` rights (system packages and PostgreSQL); server mode must run as root

## Managing instances

```bash
odoobuddy list                 # all instances, versions, ports and status
odoobuddy info shop            # paths, ports, database, source code
odoobuddy use shop             # set the "current" instance

odoobuddy start|stop|restart shop
odoobuddy logs shop            # follow the log (-n 500 --no-follow also work)
odoobuddy shell shop           # Odoo shell with `env` (--db to pick a database)

odoobuddy update shop          # pull latest code + upgrade modules in every database
odoobuddy backup shop          # database + filestore → zip (Odoo's own format)
odoobuddy restore backup.zip --name shop --db shop_copy
odoobuddy remove shop          # asks you to type "shop" to confirm

odoobuddy doctor               # check the system and every instance
odoobuddy versions             # Odoo versions available to install
```

### Which instance does a command use?

1. The name you type: `odoobuddy logs shop`
2. Otherwise, the instance folder you are in (`cd ~/odoo/shop/addons/my_module`)
3. Otherwise, the one chosen with `odoobuddy use shop`
4. Otherwise, it asks you to pick from a list. It never guesses.

## Where things go

Everything about an instance lives in **one folder**:

```
~/odoo/testing20/                 (server: /opt/odoobuddy/instances/testing20/)
├── odoo/
│   ├── odoo/                     ← Odoo Community (branch 20.0, latest commit only)
│   └── enterprise/               ← Odoo Enterprise (if chosen)
├── custom/
│   ├── addons/                   ← your own loose modules
│   ├── web/                      ← cloned repositories (odoobuddy testing20 clone URL)
│   └── my-company-modules/
├── conf/
│   └── odoo.conf                 ← configuration (ports, DB user, addons_path, master password)
├── venv/                         ← this instance's own Python
├── data/filestore/<db>/          ← attachments of each database   (server: /var/lib/odoo/<name>/)
├── logs/odoo.log                 ← log                            (server: /var/log/odoo/<name>.log)
├── backups/                      ← backup zips                    (server: /var/backups/odoobuddy/<name>/)
├── start.sh                      ← run in the terminal (local)
├── .idea/  or  .vscode/          ← PyCharm or VS Code project (local)
└── .odoobuddy                ← marks the folder, so commands know which instance you're in
```

`addons_path` order: `odoo/enterprise` → `custom/addons` → each cloned repository → Community.
Repositories are scanned for modules, so both OCA-style repos (modules at the root) and
repos with an `addons/` subfolder work.

On servers, each instance also has a system user `odoo-<name>`, a service `odoo-<name>.service`
and, with a domain, `/etc/nginx/sites-available/odoo-<name>.conf`. Every instance has its own
PostgreSQL user `odoo_<name>`, which can only see its own databases.

## Custom module repositories

The wizard asks whether to clone repositories during installation. You can also add them later:

```bash
odoobuddy testing20 clone https://github.com/OCA/web.git          # branch = Odoo version
odoobuddy testing20 clone git@github.com:acme/odoo-addons.git 20.0
odoobuddy testing20 repos                                          # list them
```

Each repository is cloned into `custom/`, its `requirements.txt` is installed into the
instance's Python, and `addons_path` in `conf/odoo.conf` is updated; your other edits to
the file are kept. For private https repositories you are asked for an access token. It is
used for that clone only and never saved. SSH URLs use your SSH keys.
`odoobuddy update` also pulls every repository.

## Automation (no questions)

Every question has a flag. For example:

```bash
export ODOO_GITHUB_TOKEN=ghp_...          # only for Enterprise
sudo -E odoobuddy install --mode server --version 19.0 --edition enterprise \
    --name shop --port 8069 --domain erp.example.com --ssl \
    --email admin@example.com --workers 5 --firewall \
    --repo https://github.com/OCA/web.git --repo git@github.com:acme/addons.git#19.0 --yes
```

Add `--dry-run` to see every command without running anything.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest && ruff check . && mypy
```

## License

MIT
