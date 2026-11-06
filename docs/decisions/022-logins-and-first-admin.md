# Logins, roles and the first admin

Date: 5 Nov 2026. Status: decided.

Milestone 6A adds local accounts with the three roles of main spec 7 (viewer, operator, admin), scrypt-hashed passwords, optional TOTP 2FA, lockout, rate limiting and 30-minute idle sessions. Every page and API needs a login except the login and first-run pages and the health check. The first admin is created on a first-run web page that is only offered from the server itself while the site has no accounts. The milestone 5 change log becomes the audit log, with the user on every entry, kept for 1 year.

Spec: main spec 5.6, 7, 8; docs/specs/m6-logins-alarms.md
