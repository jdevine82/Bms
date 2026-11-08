# Devine BMS guides

These guides cover installing, setting up and using Devine BMS. They are kept with the code: when the
software changes, the guide changes in the same pull request.

## Which guide do I need?

### For installers and support (Devine BMS staff)

| Guide | What it covers |
|---|---|
| [Development machine](install-development.md) | Setting up a computer to build, run and test the software. |
| [Site server](install-site-server.md) | Building a site server on Proxmox: the containers, the first admin, MS/TP, MQTT and alarm delivery. |
| [Pi touch screens](install-pi.md) | A Pi as the site server's touch screen, as a standby that can take over, or as a standalone BMS. |
| [Remote access](remote-access.md) | Tailscale: one tailnet per customer, the access policy, support access. |
| [Settings reference](settings-reference.md) | Every environment variable, the setup file, and the settings in the web app. |
| [Commissioning](commissioning.md) | Adding devices and points, templates, alarms, schedules, graphics, control logic and the Haystack API. |
| [Updates](updates.md) | Making a release and updating a site and its Pis, with rollback. |
| [Troubleshooting](troubleshooting.md) | Value quality, logs, common problems, and how to get back in. |

### For the customer's people

| Guide | Who reads it | What it covers |
|---|---|---|
| [Logins and users](users-and-logins.md) | The site's admins | Logging in, roles, adding people, two-factor login, HMI PINs, the audit log. |
| [Using the web app](using-the-web-app.md) | Everyone | Values and trends, graphics, commands and hand mode, schedules, control logic. |
| [The touch screen](touch-screen.md) | Operators | The HMI on the wall: its screens, the PIN, standalone and direct mode. |
| [Alarms](alarms.md) | Everyone; recipients set by admins | What the levels mean, acknowledging, who is told and how. |

## Words used in these guides

- **Site server:** the computer at the site that runs the BMS (Proxmox with two containers).
- **HMI** or **touch screen:** the Raspberry Pi with a touch screen, usually on the plant room wall.
- **Standalone:** a small site where the Pi is the whole system, with no site server.
- **Point:** one value from a device, for example a room temperature or a fan's on/off command.
- **Good, Stale, Bad:** a value's quality. Good is a fresh, valid reading. Stale means no new reading arrived in time. Bad means the device did not answer or gave an error. Stale and Bad values are never shown as live numbers.
- **Hand mode:** an operator's temporary override of a point. It returns to automatic control when its time ends.

## Where the details are

The [main specification](../../devine-bms-specification.md) is the full reference. Each milestone has a spec in
[`docs/specs/`](../specs/) and each decision a short note in [`docs/decisions/`](../decisions/).
