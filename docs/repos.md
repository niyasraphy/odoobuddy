# Custom repositories

## During installation

The wizard asks *"Clone custom module repositories now?"*. Enter one URL at a time (HTTPS or
SSH) and a branch (default: the Odoo version, e.g. `19.0`). Each repository is checked
**before** anything is downloaded:

- wrong branch → you are told immediately
- private repository → you are asked for an access token
- still unreachable → it is skipped with a message; the install continues

## Later

```bash
odoobuddy shop clone https://github.com/OCA/web.git           # branch = Odoo version
odoobuddy shop clone git@github.com:acme/odoo-addons.git 19.0
odoobuddy shop repos                                           # list them
```

For each repository, odoobuddy:

1. clones it into `custom/<name>` with full history, so you can commit and push
2. installs its `requirements.txt` into the instance's Python
3. finds its modules, whether at the root (OCA style) or in a subfolder like `addons/`
4. updates `addons_path` in `conf/odoo.conf`, keeping your other edits
5. offers to restart Odoo if it is running

`odoobuddy update shop` also pulls every repository.

## Private repositories

- **SSH URLs** (`git@...`) use your SSH keys.
- **HTTPS URLs** ask for a token, or read it from `ODOO_REPO_TOKEN`. The token is used for
  that clone only and is never saved.
