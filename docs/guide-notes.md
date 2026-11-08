# Notes for the user and installation guide

The guides are written (main spec 18, item 13b): see [`docs/guide/`](guide/README.md).

**Keeping them current:** when a change alters something people install, set up or see, update the guide in the same
pull request. Each milestone spec keeps its own "How to set up" section as the detailed record, and the guide gives the
steps a reader follows.

| Guide | Covers |
|---|---|
| `install-development.md` | Development machine, tests, command-line tools |
| `install-site-server.md` | Proxmox site server, first admin, MS/TP, MQTT, alarm secrets, standby, backups |
| `install-pi.md` | Pi touch screen, standby HMI, standalone |
| `remote-access.md` | Tailscale tailnets, policy, support sharing |
| `settings-reference.md` | Environment variables, files, setup file, web app settings |
| `commissioning.md` | Buses, Discover, the wizard, mapping table, templates, alarms, limits, schedules, graphics, logic, Haystack |
| `updates.md` | Releases, site and Pi updates, rollback |
| `troubleshooting.md` | Value quality, logs, getting back in, common problems, bench tools |
| `users-and-logins.md` | (customer admins) roles, people, 2FA, PINs, audit log |
| `using-the-web-app.md` | (customer) values, graphics, trends, commands, schedules, logic |
| `touch-screen.md` | (customer operators) the HMI, PIN, direct mode, standalone |
| `alarms.md` | (customer) levels, acknowledging, recipients |

Still to write when they exist: the pilot site's own notes (milestone 13), bench results, and the support terms and
licence check (main spec 1).
