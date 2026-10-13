# odooinstaller

**Install and manage many Odoo instances, of any version, on your laptop or on production servers, with one command.**

```bash
pip install odooinstaller
odooinstaller
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

> `odooinstaller` is a community project and is **not affiliated with Odoo S.A.**
> It is also unrelated to the `odoo-installer` package on PyPI.

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
- Python 3.10+ to run `odooinstaller` itself
- `sudo` rights (system packages and PostgreSQL); server mode must run as root

## Managing instances

```bash
odooinstaller list                 # all instances, versions, ports and status
odooinstaller info shop            # paths, ports, database, source code
odooinstaller use shop             # set the "current" instance

odooinstaller start|stop|restart shop
odooinstaller logs shop            # follow the log (-n 500 --no-follow also work)
odooinstaller shell shop           # Odoo shell with `env` (--db to pick a database)

odooinstaller update shop          # pull latest code + upgrade modules in every database
odooinstaller backup shop          # database + filestore → zip (Odoo's own format)
odooinstaller restore backup.zip --name shop --db shop_copy
odooinstaller remove shop          # asks you to type "shop" to confirm

odooinstaller doctor               # check the system and every instance
odooinstaller versions             # Odoo versions available to install
```

### Which instance does a command use?

1. The name you type: `odooinstaller logs shop`
2. Otherwise, the instance folder you are in (`cd ~/odoo/shop/addons/my_module`)
3. Otherwise, the one chosen with `odooinstaller use shop`
4. Otherwise, it asks you to pick from a list. It never guesses.

### Shared source code

Odoo's code is downloaded **once per version** and shared by all instances of that version, which saves gigabytes. `update` warns you and upgrades every instance that shares the code. To give an instance its own copy, for example to test a patch, install it with `--own-source`.

## Where things go

### Local (your computer)

```
~/odoo/
├── .sources/                     ← Odoo code, downloaded once per version, shared
│   ├── 19.0/
│   │   ├── community/            ← odoo/odoo, branch 19.0 (only the latest commit)
│   │   └── enterprise/           ← odoo/enterprise (only if you chose Enterprise)
│   └── 20.0/
│       └── community/
│
├── shop/                         ← one folder per instance
│   ├── addons/                   ← YOUR custom modules go here (already in addons_path)
│   ├── odoo.conf                 ← configuration (ports, DB user, addons_path, master password)
│   ├── venv/                     ← this instance's own Python
│   ├── data/filestore/<db>/      ← attachments of each database
│   ├── logs/odoo.log             ← log when started with `odooinstaller start`
│   ├── backups/                  ← `odooinstaller backup` zips
│   ├── start.sh                  ← run in the terminal
│   ├── .idea/  or  .vscode/      ← IDE project (PyCharm or VS Code)
│   └── .odooinstaller            ← marks the folder, so commands know which instance you're in
│
└── test20/                       ← another instance, same layout
```

`addons_path` order: Enterprise → your `addons/` → Community `addons/` → Community `odoo/addons/`.
With `--own-source`, the code lives in `~/odoo/<name>/src/community` (and `src/enterprise`) instead.

### Server (production)

```
/opt/odooinstaller/
├── sources/<version>/community/  ← shared Odoo code (enterprise/ next to it)
├── instances/<name>/addons/      ← YOUR custom modules
├── instances/<name>/venv/        ← Python
└── python/                       ← Python versions used by the instances

/etc/odoo/<name>.conf             ← configuration
/var/lib/odoo/<name>/filestore/   ← attachments
/var/log/odoo/<name>.log          ← log (rotated weekly)
/var/backups/odooinstaller/<name>/← backups
/etc/systemd/system/odoo-<name>.service
/etc/nginx/sites-available/odoo-<name>.conf
```

Each server instance runs as its own system user `odoo-<name>`, and every instance has its
own PostgreSQL user `odoo_<name>` (it can only see its own databases).

## Automation (no questions)

Every question has a flag. For example:

```bash
export ODOO_GITHUB_TOKEN=ghp_...          # only for Enterprise
sudo -E odooinstaller install --mode server --version 19.0 --edition enterprise \
    --name shop --port 8069 --domain erp.example.com --ssl \
    --email admin@example.com --workers 5 --firewall --yes
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
