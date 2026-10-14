# Local development

## How Odoo runs

The wizard asks **"How do you want to run Odoo?"** and lists the editors installed on your
computer first. Editors are never installed or changed; only a project folder (`.vscode/`,
`.idea/` or `.zed/`) is written inside the instance.

| Choice | After the install | Debugging |
|---|---|---|
| **VS Code, Cursor, Windsurf, VSCodium** | The editor opens and Odoo **starts by itself** in the terminal panel "Odoo: run NAME" | *Run and Debug* → **Attach to Odoo (NAME)** → F5, any time |
| **PyCharm** | PyCharm opens and Odoo **starts by itself** (Startup Tasks) in the Run window | Stop it (■), then click **Debug** 🐞 |
| **Zed** | Zed opens; start Odoo with `Ctrl+Shift+P` → *task: spawn* → "Odoo: run NAME" | `Ctrl+Shift+P` → *debugger: start* → **Attach to Odoo** |
| **Terminal** | `odoobuddy run NAME` shows live logs; Ctrl+C stops it | Any editor can still attach |
| **Background** | Started for you; `odoobuddy start / stop / logs NAME` | Any editor can still attach |

!!! tip "First time only"
    - **VS Code family:** trust the folder, and click **Allow** when asked about automatic
      tasks.
    - **PyCharm:** click **Trust Project**. If asked for an interpreter, choose *Existing* →
      `~/odoo/NAME/venv/bin/python`.

### Attach to Odoo

Every way of starting Odoo listens for a debugger on a per-instance port (web port +
20000, e.g. 8069 → 28069). "Attach to Odoo" connects to the **running** Odoo, so you don't
restart it to debug, and it keeps working after Odoo reloads itself when you change Python
code. Several instances can be debugged at the same time.

### Change editor later

```bash
odoobuddy open shop                 # open it again
odoobuddy open shop --with pycharm  # switch editor (writes that editor's project files)
```

If Odoo is already running in the background, `open` offers to stop it first, because the
editor needs the port.

## Where things are

```
~/odoo/shop/
├── odoo/
│   ├── odoo/                 ← Odoo Community (one branch, latest commit only)
│   └── enterprise/           ← Odoo Enterprise (if chosen)
├── custom/
│   ├── addons/               ← your own modules
│   └── <repository>/         ← cloned repositories
├── conf/odoo.conf            ← configuration
├── venv/                     ← this instance's Python
├── data/filestore/<db>/      ← attachments
├── logs/odoo.log             ← log when started in the background
├── backups/                  ← backup zips
├── start.sh                  ← run in a terminal without odoobuddy
└── .vscode/ .idea/ or .zed/  ← editor project
```

`addons_path` order: `odoo/enterprise` → `custom/addons` → each repository → Community.

## Several versions side by side

Every instance is independent, so Odoo 17, 18, 19 and 20 can run together. Ports are
suggested automatically, and every command tells you which instance it acts on:

1. the name you type (`odoobuddy logs shop`), or
2. the instance folder you are in, or
3. the one chosen with `odoobuddy use shop`, or
4. it asks; it never guesses.
