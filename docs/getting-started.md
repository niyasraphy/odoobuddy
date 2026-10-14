# Getting started

## Install odoobuddy

```bash
sudo apt install pipx      # if you don't have it yet
pipx install odoobuddy
odoobuddy --install-completion   # optional: Tab completion for commands and instance names
```

`pip install odoobuddy` works too, but `pipx` keeps it in its own environment.

## Your first instance

Run `odoobuddy` (or `odoobuddy install`) and answer the questions:

| Question | Notes |
|---|---|
| Local or server | Local is for development on your computer; server is for production |
| Odoo version | Read live from Odoo's GitHub, so new versions appear automatically |
| Edition | Enterprise needs a GitHub token with access to `odoo/enterprise` |
| Instance name | Used for the folder, database user and service, e.g. `shop` |
| Port | The next free port is suggested (8069, then 8169, 8269, ...) |
| Custom repositories | Optional, can also be added later |
| How to run Odoo | Local: in an editor, a terminal or the background |
| Domain, SSL, workers, firewall | Server only |

Then a summary appears with **Install now / Change a setting / Cancel**. "Change a
setting" asks only that question again.

During the install, odoobuddy asks **once** for your sudo password (system packages and
PostgreSQL), then shows each step with a live progress line and its duration.

## Your first database

The installer does **not** create a database for you. It proves the setup works by
installing a real module (`contacts`) in a temporary database, then deletes it.

At the end you get the database manager address and the **master password**:

1. Open `http://localhost:8069/web/database/manager`
2. Enter the master password
3. Choose the database name, language, country, demo data and admin login

## Enterprise

Choose *Enterprise* and paste a GitHub token from an account with access to
`odoo/enterprise`. The token is checked before anything is downloaded, used only for the
download and never saved. If it has no access, you can try another token or continue with
Community.

For scripts: `export ODOO_GITHUB_TOKEN=ghp_...` instead of typing it.

## No questions (automation)

Every question has a flag:

```bash
odoobuddy install --mode local --version 19.0 --edition community --name shop \
  --port 8069 --run-with vscode --no-repos --yes
```

Add `--dry-run` to see every command without running anything.
