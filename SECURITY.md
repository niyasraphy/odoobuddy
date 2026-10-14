# Security policy

odoobuddy runs with administrator rights and handles database passwords and access tokens,
so security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately instead:

- GitHub: [Report a vulnerability](https://github.com/niyasraphy/odoobuddy/security/advisories/new)
- or email **niyasraphyk@gmail.com** with "odoobuddy security" in the subject

Include what you found, how to reproduce it, and the odoobuddy version. You will get an
answer within a few days, and a fixed release as soon as possible. Credit is given in the
changelog unless you prefer otherwise.

## Supported versions

Only the latest release receives security fixes.

## How odoobuddy handles secrets

- GitHub and repository tokens are passed to git through environment variables, never in
  URLs, command lines, config files or logs, and they are not saved.
- Database passwords and SQL are sent on stdin, not on the command line.
- Generated passwords are random; `odoo.conf` files are readable only by their owner
  (local) or the instance's system user (server).
- `pull` connects with your own SSH keys and never asks for or stores passwords.
