# Production servers

Run the wizard as root and choose **Server**:

```bash
sudo pipx install odoobuddy     # or: sudo pip install odoobuddy
sudo odoobuddy
```

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
/opt/odoobuddy/instances/NAME/     odoo/odoo, odoo/enterprise, custom/, conf/odoo.conf, venv/
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
odoobuddy update shop               # backup, pull new code, upgrade modules
```

Production actions (update, restore, remove) ask you to **type the instance name**, and
every command shows a red **PRODUCTION** label.
