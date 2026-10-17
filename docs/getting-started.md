# Getting started

## Install odoobuddy

```bash
sudo apt install pipx      # if you don't have it yet
pipx install odoobuddy
pipx ensurepath && source ~/.bashrc   # first time only: puts ~/.local/bin on your PATH
odoobuddy --install-completion   # optional: Tab completion for commands and instance names
```

If you see `odoobuddy: command not found` right after installing, `~/.local/bin` is not on your
PATH yet: run `pipx ensurepath`, then open a new terminal (or `source ~/.bashrc`).

Use `pipx`, not `pip`: on Ubuntu 24.04 and newer, `pip install` outside a virtual environment
stops with `error: externally-managed-environment`.

## Your first instance

Run `odoobuddy install` and answer the questions:

First odoobuddy shows what the machine has (OS, CPUs, RAM, free disk). **Nothing is installed
while you answer**: at the end you review every answer and can change any of them, start over
or cancel. Each question shows its place, e.g. `[3/5]` (the number depends on the purpose), and
moving through a menu shows a short explanation of the highlighted choice.

| Question | Notes |
|---|---|
| What is this Odoo for? | **Production** or **Test / staging** (on this server), or **Development** (on this computer). Preselected from where you run it. On a server (no desktop) Development is not offered; use `--purpose development` to choose it anyway |
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

Then the review screen (**Review: nothing is installed yet**) lists your answers, with
**Install now / Dry run / Change a setting / Show the command for scripts / Start over /
Cancel**. *Dry run* only lists the steps and commands; nothing is set up.
"Change a setting" asks only that question again, and the review marks what changed
(`← changed`) so you can check it before installing.

During the install, odoobuddy asks **once** for your sudo password (system packages and
PostgreSQL), then shows each step with a live progress line and its duration.

## Tab completion

After the first install, odoobuddy offers **Tab completion**: press Tab to finish its commands
and your instance names, like you do with file names:

```
odoobuddy st<Tab>        →  odoobuddy start
odoobuddy logs od<Tab>   →  odoobuddy logs odoo19
```

It is optional and only adds one line to `~/.bashrc` (or `~/.zshrc`; a file for fish). It works
in new terminals, or in the current one after `source ~/.bashrc`. Turn it on any time with
`odoobuddy --install-completion`, as your own user (not with sudo: that would set it up for root).

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
