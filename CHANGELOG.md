# Changelog

## [0.2.0] - 2026-10-14

### Fixed
- Server install on Ubuntu 24.04 failed at "Create Python environment" (`python-ldap`:
  `Python.h: No such file or directory`): uv picked the system Python 3.12, which has no
  headers. Instances now always use a uv-managed Python.
- Servers without a domain showed `http://localhost:PORT` after the install and in `info`,
  which does not open from your browser. They now show `http://SERVER-IP:PORT` with the
  server's detected IP.
- Installs on a freshly booted server no longer fail while Ubuntu's automatic updates hold
  apt's lock: odoobuddy waits up to 10 minutes and says what it is waiting for.
- A step that prints nothing for 2 minutes now says it is probably still working, where the
  full log is, and that Ctrl+C is safe (running the same command again continues).
- `logs` looked stuck: it now says it is following new lines (Ctrl+C to stop) and warns
  when the log is missing or empty, with the commands to find out why.
- Choosing an editor that is not installed now says so, shows how to install it, and offers to check again, use it later, or pick another way to run Odoo (the summary no longer calls it "your installed" editor)
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
- The wizard starts by asking what the Odoo is for, and adapts to it:
  - **Production** asks every question, then a *production check* points out risky
    choices (no HTTPS, no backups, firewall off, more workers than the free RAM can hold,
    low disk space) and can apply the recommended settings in one step.
  - **Test / staging** follows the production instance on the server (its version,
    edition and a name like `erp-test`), uses 2 workers by default and no backups.
  - **Development** asks only what matters on your computer.
  The purpose is preselected from where odoobuddy runs, stored with the instance, and
  shown as a red PRODUCTION, orange TEST or green DEV label in banners and `list`.
  `--purpose production|test|development` does the same from scripts.
- Before the first question, the install shows what the machine has (OS, CPUs, RAM, free
  disk), whether Ubuntu is busy installing updates, another web server on port 80, and the
  instances already there.
- The install summary offers "Start over", and the `odoobuddy install ... --yes` command that
  repeats the same install.
- `doctor` warns when a production instance has no nightly backups.
- After an install, odoobuddy offers to turn on Tab completion (bash, zsh, fish) when it
  is not on yet; `odoobuddy --install-completion` still does it by hand.
- A new welcome screen: framed, with the odoobuddy logo, your instances and the commands
  to start with.
- A roomier install screen: the machine check in a frame, "Setup" and "Installing" section
  lines, hints indented under their question, a grouped summary, and the real server IP in
  the "without a domain" hint. The command that repeats the install is now a menu choice
  ("Show the command for scripts"), one option per line, without `sudo` when you are root.
- During long install steps a little buddy walks along, peeks whether the step is done,
  checks his watch and has a coffee. `ODOOBUDDY_NO_ANIMATION=1` turns him off.
- Nightly backups on servers: the wizard offers them (on by default), and
  `odoobuddy backup NAME --nightly` / `--no-nightly` turns them on or off later. Every
  database is backed up around 02:30 and the last 7 are kept, in `/var/backups/odoobuddy/NAME/`.
  `backup` also gained `--all` (every database) and `--keep N` (delete older backups).
- odoobuddy's own run logs (`/var/log/odoobuddy/`, `~/.local/share/odoobuddy/logs/`) are
  deleted after 30 days, so nightly backups do not pile up a log file per night.
- Before installing, odoobuddy checks free memory and disk space for the new instance and,
  when they look too small, warns and asks "Install anyway, at your own risk?".
- The number of workers is limited to what the server's CPU and RAM can run next to its
  other instances; a larger number is refused.
- `odoobuddy` alone shows a welcome screen with your instances and the commands to start with.
- New commands: `status` (running or stopped, and the address) and `support` (documentation,
  bug reports and contact).
- `odoobuddy -h`, `odoobuddy help` and `odoobuddy help COMMAND` show the help.
- Missing editor: the wizard (and `odoobuddy open --with`) can install Zed with its official installer, and VS Code, VSCodium and PyCharm through snap; for others it shows the download link and waits until the editor is installed
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
- Only production instances ask you to type their name before `remove`, `restore` and
  `update`, and only production is backed up before `update`; test and development
  instances ask yes/no. Server instances installed before this version count as production.
- The wizard's first question "Where are you installing Odoo?" (local or server) is now
  "What is this Odoo for?"; `--mode` still works (server = production, local = development).
- The domain is asked in one question ("Enter" for none) instead of yes/no plus the name.
- Server instances live in `/opt/odoo/NAME` (was `/opt/odoobuddy/instances/NAME`). Existing
  instances keep their folders and keep working.
- The install wizard starts with `odoobuddy install`; `odoobuddy` alone shows the welcome screen.
- Yes/no questions wait for Enter, so a mistyped key can be corrected.
- Colours in the install result and `info`: links (clickable), folders and files, commands to type, keyboard shortcuts, the master password and section headings each have their own colour
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
