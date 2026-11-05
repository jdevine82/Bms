# Milestone 6: logins, audit log and alarms

Status: **planned** (5 Nov 2026). Main spec: 5.5 and 5.5.1 (alarms and recipients), 5.6 (audit log), 7 (users, roles and login rules), 8 (security), 18 (milestone 6).

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How it is split | **Two parts.** **6A:** logins, roles and the audit log (replaces the localhost-only guard of milestone 5). **6B:** everything about alarms: the alarm engine, the alarm screens, acknowledge, comment and clear, history, recipients and delivery. |
| 2 | SMS provider | **ClickSend** for the first SMS plug-in. The sender is swappable (main spec 5.5), so another provider is one more plug-in. |
| 3 | ntfy server | **A site setting**, defaulting to the public `https://ntfy.sh`. A site can point at a self-hosted ntfy server later without code changes. |
| 4 | Email | **SMTP**: host, port, TLS mode, user name and sender address in the site settings; the password only in an environment variable named there (as MQTT brokers do). Works with Microsoft 365, Gmail, an ISP or a relay. |
| 5 | The first admin | **A first-run web page**: while the site has no accounts, the web app asks for the first admin's name and password. **It is only offered from the server itself** (127.0.0.1 or ::1), the same guard milestone 5 used, so nobody else on the LAN can claim a new site. Once an account exists the page is gone. |

## 6A: logins, roles and audit log

### Accounts and roles (main spec 7)

- Accounts live in the site database. Roles: **viewer, operator, admin** (the table in main spec 7). The platform admin account is an admin account the site's first admin (or the first-run page) creates; it is marked `platform` so it is easy to find, with no extra rights.
- Passwords: at least 10 characters, stored with **scrypt** (Python standard library, a random salt per password). No default passwords.
- **Optional authenticator-app 2FA** (TOTP, RFC 6238, 30-second codes), set up from the user's own page with a QR code. An admin can require it for the admin role.
- **Lockout:** 5 failed logins lock the account for 10 minutes; an admin can unlock it. **Rate limiting** on the login endpoint per client address, always on. Failed logins and lockouts go to the audit log.
- **Sessions:** a random token in an HttpOnly, SameSite=Strict cookie; the database keeps only its hash. **30 minutes idle** ends a session. Logging out ends it at once; changing a password ends that user's other sessions.
- The login rules (length, idle time, lockout count and time, 2FA for admins) are site settings an admin can change, with the defaults above.

### What needs a login

- **Every page and API needs a login** except the login page, the first-run page (from the server itself only) and `/api/health`.
- Reading (points, trends, alarms): any role. Setup changes, Discover, test reads, users and settings: **admin**. Acknowledging alarms (6B): operator and admin.
- **The HMI kiosk** (milestone 3) shows values without a person logging in. **[Assumed]** Kiosks are listed by address in the site settings and get viewer access without a login; commands on the HMI need a PIN when commands arrive (milestone 7). PINs are set up then.
- The localhost-only guard of milestone 5 is replaced by the admin role.
- **Allowed networks** (main spec 8): an optional list in the site settings; a login from outside it is refused.

### Audit log (main spec 5.6)

- The milestone 5 change log becomes the **audit log**: each entry has the time, **the user** (and the client address), what was done, and before and after. It also records logins, failed logins, lockouts, logouts, user and settings changes, and (6B) alarm acknowledgements and comments.
- **Kept for 1 year**, then deleted automatically.
- **Viewer and export:** admins see everything, with filters (user, type, item, date), and can export CSV. Operators see their own actions. Nobody can edit or delete entries from the app.

### Screens

- Login page (with the 2FA code when the account has it), first-run page, and **My account** (change password, set up or remove 2FA, sign out everywhere).
- **Users** (admin): add, change role, disable, reset a password, unlock, require 2FA.
- **Site settings → Security**: login rules, kiosk addresses, allowed networks.
- **Audit log** replaces the change log page.

## 6B: alarms

### Alarm definitions (main spec 5.5)

- Types: **high limit, low limit, state** (a Bool or Str point in a given state), **device offline** and **stale data**. Each has a **level** (Info, Warning, Critical), a **delay** (the condition must hold this long), a **deadband** for limits, and a short message without sensitive detail.
- Alarms are part of the setup: on a point (`alarms` in the config file), and as defaults on equipment template roles, where an instance can override them (an override always wins, as in 5A).
- Commissioning (5.1.1 step 3) and the mapping table show alarms in their checklists.

### The alarm engine

- Runs in the backend on the live values. An alarm is **active** while its condition holds, and **returns to normal** when it clears. **Acknowledge** (operator or admin) with an optional comment; comments can be added at any time. An alarm leaves the active list once it is acknowledged and back to normal.
- Bad or Stale values do not trigger limit or state alarms (main spec 5.2.1); they raise the offline or stale alarms instead, so one fault does not cause a flood.
- **History** of every alarm: raised, returned to normal, acknowledged, comments, with who and when. Kept with the audit log's retention.
- Live updates over the existing WebSocket, so the screens and the HMI alarm list update at once.

### Delivery (main spec 5.5.1)

- **Site recipient list:** name, email, phone, ntfy topic, and a tick per level. Critical cannot be muted, and the settings warn when nobody receives Critical.
- **Default channels** per level as main spec 5.5 (Info: screen only; Warning: email and ntfy; Critical: email, ntfy and SMS), changeable per site.
- **Quiet hours** per person for Warning only: held and sent as one summary when the window ends.
- Senders: **SMTP email, ClickSend SMS, ntfy push** (server from the site settings). Credentials only in environment variables named in the settings. Failed sends are retried with back-off while the internet is down.
- **Test message** to any recipient with the result, and a **delivery log** (who, channel, sent, failed or held) shown with the alarm history.

### Screens

- **Alarms** page in the web app: active alarms (level by text and shape as well as colour), acknowledge with comment, history with filters, delivery log. Alarm settings on points in the setup screens and mapping table.
- **Recipients and channels** in the site settings, with test messages.
- The HMI alarm list (milestone 3 shell) shows active alarms.

## Testing

- Backend tests for login, lockout, rate limits, sessions, 2FA, role checks on every setup route, the audit log and its retention; the alarm engine (delays, deadbands, quality, return to normal, acknowledge); delivery with fake SMTP, ClickSend and ntfy servers.
- Browser tests: first run, login and logout, an operator cannot reach setup, an alarm raised by the simulator appears, is acknowledged and is in the history.
- Bench: a real email, SMS and ntfy push once the accounts exist (the keys are supplied as environment variables, never committed).
