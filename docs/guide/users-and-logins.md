# Logins and users

For the site's admins. This guide explains who can do what, how to add people, and how logins are kept safe.

## Logging in

Open the site's address in a browser:
- on the site network, the address your installer gave you, for example `http://192.168.1.20/`;
- from elsewhere, `https://bms-<site>.<your tailnet>.ts.net/`, with the Tailscale app on.

Enter your user name and password. If you have two-factor login, you are also asked for the code from your
authenticator app.

You are signed out after 30 minutes without using the site. **Sign out** (top right) signs you out at once.

Your own page opens when you click your name at the top: **My account**. There you can:
- change your password;
- turn two-factor login on or off;
- set your touch-screen PIN;
- **Sign out everywhere** (every browser where you are logged in);
- see your recent actions.

## Roles

Each person has one role on the site.

| What they can do | Viewer | Operator | Admin |
|---|---|---|---|
| See graphics, values, trends, alarms and schedules | Yes | Yes | Yes |
| Acknowledge and comment on alarms | | Yes | Yes |
| Change setpoints, switch equipment, hand mode | | Yes, within the limits | Yes, within the limits |
| Change schedules and holiday calendars | | Yes | Yes |
| Set up devices, points, rooms, templates, graphics and control logic | | | Yes |
| Manage users, PINs, site settings and updates | | | Yes |
| See the audit log | | Their own actions | Everything |

Write limits apply to everyone, admins too: nobody can set a value outside the range set for that point.

## Adding and changing people

**Setup → Users** (admins):
- **Add:** the person's name, a user name, their role and a first password (at least 10 characters). Tell them to change
  it on My account.
- **Change** a person's role (it applies from their next login), disable the account, set a new password, unlock it, or
  remove their touch-screen PIN or two-factor login.
- **Remove** an account. The audit log keeps everything they did.

The site always keeps at least one enabled admin.

There are two special kinds of account:
- **Platform admin account:** the account Devine BMS support uses. It is an admin account, marked so it is easy to find.
  Every support login is recorded in the audit log as **support-login**, with the time and the Tailscale identity. You
  can ask for support access to be removed.
- **Haystack API client:** an account for a program (an analytics or energy platform), not a person. It can only read
  the site through the Haystack API, and cannot log in to the web app or the touch screen. The API itself is switched
  on in Setup → Security.

## Keeping logins safe

- **Passwords** are at least 10 characters. There are no shared or default passwords.
- **Lockout:** after 5 wrong passwords (or PINs), an account is locked for 10 minutes. An admin can unlock it earlier on
  Setup → Users.
- **Two-factor login (2FA):** each person can turn it on in My account with an authenticator app (scan the QR code).
  Setup → Security can require it for admins. **Lost phone:** an admin removes the person's 2FA on Setup → Users, and they
  set it up again.
- **Allowed networks** (Setup → Security): if set, logins are accepted only from those networks. On the site network,
  passwords are sent unencrypted, so keep the web app on a network only staff use. Logins over Tailscale are encrypted.
- The rules (password length, the idle time, the lockout) can be changed in Setup → Security.

**Forgotten admin password, and no other admin?** Ask Devine BMS support. They can set a new one on the server.

## The touch screen and PINs

The touch screen on the wall shows the site without anyone logging in, but only to **view**. To change anything, a
person unlocks it with their **PIN**:

1. Each operator or admin sets a PIN on **My account → HMI PIN** (4 to 12 digits, not all the same digit, not a run like
   1234).
2. On the touch screen, they tap their name and enter the PIN.
3. It stays unlocked for 5 minutes without use, or until **Lock now**.

A PIN only works on a touch screen listed in Setup → Security → HMI kiosks. An admin can remove a person's PIN on
Setup → Users.

## The audit log

**Setup → Audit log** records who did what and when:
- logins, failed logins and lockouts;
- commands and hand modes, with the old and new values;
- changes to the setup, schedules and settings;
- alarm acknowledgements and comments;
- updates.

Filter by person, type, item or date, open **Before and after** on an entry, or export a CSV. Entries are kept for
one year and cannot be changed or deleted from the app.
