# Alarm delivery: SMTP email, ClickSend SMS, ntfy push

Date: 5 Nov 2026. Status: decided.

Alarm email goes out over SMTP (settings per site, password in an environment variable). The first SMS plug-in is ClickSend; the sender stays swappable. The ntfy server is a site setting that defaults to the public ntfy.sh, so a site can move to a self-hosted server without code changes. Credentials are never in the config file or the repository.

Spec: main spec 5.5, 5.5.1; docs/specs/m6-logins-alarms.md
