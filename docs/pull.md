# Copy production to local

Bring a production database **and its files** to your computer, made safe for development:

```bash
odoobuddy pull root@erp.example.com:shop --into shop-dev
# or:  odoobuddy shop-dev pull root@erp.example.com:shop
```

## What happens

1. Connects over **SSH with your keys** (no passwords; the remote user must be root or have
   password-less sudo).
2. Reads the server's odoobuddy instance to find the database, filestore and Odoo
   version, and **refuses** to copy into a different Odoo version.
3. Streams the database and the filestore to your computer. Nothing is left on the server.
4. Restores into a new local database, `<db>_<date>` by default (`--as NAME` to choose).
   Replacing an existing database asks you to type the instance name.
5. **Neutralizes** the copy with Odoo's own `neutralize`: outgoing mail servers, scheduled
   actions, payment providers and other integrations are turned off.

!!! danger "Neutralizing"
    `--no-neutralize` keeps everything active: the copy could **send real emails to
    customers** or run payments. If neutralizing fails, odoobuddy stops and tells you to
    drop the copy.

Users and passwords are the same as on the server.

## Options

| Option | Meaning |
|---|---|
| `--db NAME` | Which server database (asked if there are several) |
| `--as NAME` | Local database name |
| `--no-filestore` | Copy only the database (faster, attachments missing) |
| `--ssh-port 2222` | SSH port, if not 22 |
| `--yes` | Replace an existing local database without asking |

## Servers not installed by odoobuddy

```bash
odoobuddy pull root@old-server --into shop-dev --db production \
  --remote-filestore /opt/odoo/.local/share/Odoo/filestore
```
