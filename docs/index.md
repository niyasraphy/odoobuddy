# odoobuddy

**Install and manage many Odoo instances, of any version, on your laptop or on production
servers, with one command.**

![odoobuddy demo](assets/demo.gif)

```bash
sudo apt install pipx        # if you don't have it yet
pipx install odoobuddy
pipx ensurepath && source ~/.bashrc   # first time only: puts ~/.local/bin on your PATH
odoobuddy install
```

The wizard checks the machine, asks what the Odoo is for (production, test or development),
asks only the questions that purpose needs, shows a summary you can change, and sets
everything up. For production it also reviews risky choices and recommends safer ones.

## What it does

<div class="grid" markdown>

- **Any Odoo version**, read live from Odoo's GitHub (16.0 to 20.0 today), with the right
  Python installed automatically.
- **Community or Enterprise.** Your GitHub token is checked before downloading, and the
  install falls back to Community if it has no access.
- **Many instances side by side**, each in one folder with its own code, Python, config,
  database user, ports and logs.
- **Your editor, ready to debug:** VS Code, Cursor, Windsurf, VSCodium, PyCharm or Zed opens
  after the install, with Odoo running inside it. See [Local development](local.md).
- **Production servers:** systemd, Nginx, free SSL, firewall, tuned workers and log
  rotation. See [Production servers](server.md).
- **Custom repositories** cloned into the instance, with `addons_path` and Python
  requirements handled for you. See [Custom repositories](repos.md).
- **Production → local** in one command, neutralized so the copy can't send emails. See
  [Copy production to local](pull.md).
- **Safe by default:** `--dry-run`, typed confirmation for anything destructive, a backup
  before production updates, and secrets that are never shown on command lines or in logs.

</div>

## Requirements

- Linux: Ubuntu 22.04 / 24.04 or Debian 12 (others may work)
- Python 3.10 or newer to run odoobuddy itself
- `sudo` rights; server mode runs as root

!!! note
    odoobuddy is a community project and is **not affiliated with Odoo S.A.**
