# FAQ & troubleshooting

### The install seems stuck at the first step
It may be waiting for your sudo password. odoobuddy asks for it once, before the steps
start. If you used an older version, press Ctrl+C and run it again.

### Where is the log?
Each run prints its log path at the start: `~/.local/share/odoobuddy/logs/` (local) or
`/var/log/odoobuddy/` (server). Odoo's own log: `odoobuddy logs NAME`.

### "Port already in use" when starting from my editor
Odoo is already running elsewhere, often in the background. Check `odoobuddy list`, then
`odoobuddy stop NAME`. `odoobuddy open NAME` offers to do this for you.

### VS Code didn't start Odoo when the folder opened
Trust the folder, and allow automatic tasks when VS Code asks (or run the command
*Tasks: Manage Automatic Tasks* → *Allow*). You can always start it from
*Terminal → Run Task → "Odoo: run NAME"*.

### PyCharm didn't start Odoo
Click **Trust Project**, then check *Settings → Tools → Startup Tasks*: it should list
"Odoo VERSION (NAME)". You can always click ▶ with that configuration selected.

### Odoo stopped after I changed Python code
Odoo restarts itself when Python code changes (dev mode). Outside an activated virtual
environment it used to restart with the system Python and crash with "No module named
...". odoobuddy fixes this with a small startup hook in each instance's Python; older
instances get it automatically on `run`, `open` or `start`.

### Does odoobuddy change my editor?
No. It only writes `.vscode/`, `.idea/` or `.zed/` inside the instance folder.

### How do I uninstall?
Remove each instance with `odoobuddy remove NAME`, then `pipx uninstall odoobuddy`.

### Is it affiliated with Odoo?
No. odoobuddy is a community project, not affiliated with Odoo S.A.
