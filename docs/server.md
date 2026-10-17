# Production and test servers

Run the wizard as root and choose **Production** or **Test / staging** (on a server over SSH,
Production is preselected):

```bash
sudo apt install pipx
sudo PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install odoobuddy
sudo odoobuddy install
```

The two variables put the `odoobuddy` command in `/usr/local/bin`, where both `sudo` and every
user can find it. A plain `sudo pipx install odoobuddy` puts it in `/root/.local/bin`, which is
not on the PATH and which `sudo` does not search, so you would get `odoobuddy: command not found`.
To upgrade later: `sudo PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx upgrade odoobuddy`.

Before the first question, odoobuddy shows what the server has (OS, CPUs, RAM, free disk),
whether Ubuntu is busy installing updates, another web server on port 80, and the instances
already installed.

## Production or test

| | Production | Test / staging |
|---|---|---|
| Questions | all of them | version, edition, name, domain, workers |
| Version and edition | your choice | default to the production instance's |
| Name | your choice | e.g. `erp-test` next to `erp` |
| Workers | up to what the server can run | 2 by default, so production keeps the server |
| Firewall | asked (on by default) | left unchanged |
| Nightly backups | asked (on by default) | off |
| Backup before `update` | yes | no |
| `remove`, `restore`, `update` | type the instance name to confirm | yes/no |
| Label in every command | red **PRODUCTION** | orange **TEST** |

Settings a test instance does not ask about can still be changed from the summary
("Change a setting").

### The production check

After the last question, odoobuddy reviews a production install and points out risky
choices, each with a recommendation:

- no domain, so Odoo uses plain HTTP and passwords travel unencrypted
- a domain without HTTPS
- nightly backups off
- the firewall off
- more workers than the free RAM can hold
- too little free disk space

Choose **Apply the recommendations** to fix what can be fixed in one step, or **Keep my
choices**. A domain cannot be added for you: choose *Change a setting → Domain name* in the
summary.

The worker question never accepts more than the server can run: its CPUs (2 × CPU + 1) or
RAM (about 300 MB per worker), minus the workers of the other instances.

## What gets set up

- A dedicated system user `odoo-NAME`; Odoo never runs as root
- PostgreSQL, wkhtmltopdf (with headers and footers), and the system libraries Odoo needs
- A **systemd** service `odoo-NAME`, which starts on boot and restarts after crashes
- **Nginx** in front of Odoo, routing `/websocket` for live chat and notifications
- **Let's Encrypt SSL** with automatic renewal (with a domain)
- A **ufw firewall** that keeps your current SSH port open
- Workers tuned to your CPU and RAM, memory and time limits, and log rotation

The installer checks that the service answers after starting it.

## With or without a domain

| | With a domain | Without a domain |
|---|---|---|
| Address | `https://erp.example.com` | `http://SERVER-IP:8069` |
| Nginx | listens on 80/443 | listens on your chosen port |
| SSL | Let's Encrypt (optional) | no |
| Odoo itself | 127.0.0.1 only | 127.0.0.1 only, on internal ports (+10000) |

Before requesting a certificate, odoobuddy checks that the domain points to the server.
If it doesn't, the wizard shows the exact DNS record to add (type `A`, the host name, and
this server's IP) and lets you choose:

- **Check again**, after you have added the record (it asks a public DNS server, so a
  record you just added shows up without waiting for local caches)
- **Continue without a domain**, using `http://SERVER-IP:PORT`
- **Keep the domain anyway** and add SSL later

If the certificate still fails (DNS not ready yet), the install finishes over HTTP and
prints the exact `certbot` command to run later.

## Where things are

```
/opt/odoo/NAME/                   odoo/odoo, odoo/enterprise, custom/, conf/odoo.conf, venv/
/var/lib/odoo/NAME/                filestore
/var/log/odoo/NAME.log             log (rotated weekly)
/var/backups/odoobuddy/NAME/       backups
/etc/systemd/system/odoo-NAME.service
/etc/nginx/sites-available/odoo-NAME.conf
```

## Daily operations

```bash
odoobuddy list                      # all instances and their status
odoobuddy logs shop                 # follow the log
odoobuddy restart shop
odoobuddy backup shop               # database + filestore → zip
odoobuddy backup shop --nightly     # every database each night, the last 7 kept (--no-nightly stops it)
odoobuddy update shop               # backup, pull new code, upgrade modules
```

Production actions (update, restore, remove) ask you to **type the instance name**, and
every command shows a red **PRODUCTION** label (orange **TEST** for test instances).
`odoobuddy doctor` warns when a production instance has no nightly backups.
