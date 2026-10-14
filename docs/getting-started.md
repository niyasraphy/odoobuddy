# Getting started

## Install odoobuddy

```bash
sudo apt install pipx      # if you don't have it yet
pipx install odoobuddy
odoobuddy --install-completion   # optional: Tab completion for commands and instance names
```

`pip install odoobuddy` works too, but `pipx` keeps it in its own environment.

## Your first instance

Run `odoobuddy install` and answer the questions:

First odoobuddy shows what the machine has (OS, CPUs, RAM, free disk). Then:

| Question | Notes |
|---|---|
| What is this Odoo for? | **Production** or **Test / staging** (on this server), or **Development** (on this computer). Preselected from where you run it |
| Odoo version | Read live from Odoo's GitHub, so new versions appear automatically |
| Edition | Enterprise needs a GitHub token with access to `odoo/enterprise` |
| Instance name | Used for the folder, database user and service, e.g. `shop` |
| Port | Production only; others get the next free port (8069, then 8169, ...) |
| Custom repositories | Production only; others can add them from the summary or later |
| How to run Odoo | Development: in an editor, a terminal or the background |
| Domain, SSL, workers | Production and test |
| Firewall, nightly backups | Production |

A production install ends with a **production check** that points out risky choices and
can apply the recommended settings. See [Production and test servers](server.md).

Then a summary appears with **Install now / Change a setting / Start over / Cancel**, and
the command that repeats the same install from a script. "Change a setting" asks only that
question again.

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
odoobuddy install --purpose development --version 19.0 --edition community --name shop \
  --port 8069 --run-with vscode --no-repos --yes
```

Add `--dry-run` to see every command without running anything.
