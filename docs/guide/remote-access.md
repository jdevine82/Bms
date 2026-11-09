# Remote access with Tailscale

For installers and support. Decision 030; spec `docs/specs/m12a-site-server-remote.md`.

There is no cloud portal and no port forwarding. People reach a site's web app over **Tailscale**, with HTTPS from
`tailscale serve` on the Proxmox host. On the site LAN the web app stays plain HTTP.

## How it is organised

- **One tailnet per customer.** The customer's people join the customer's tailnet, and can only ever reach that
  customer's site servers.
- **One tag per site:** each site server joins as `tag:bms-site-<site>`. A policy generated from a short list decides
  which people reach which site.
- **Support by node sharing:** each site server is **shared** from the customer's tailnet into the Devine BMS support
  tailnet. Support uses one tailnet for every site. The customer sees the share and can revoke it, as the support terms
  say (main spec 1).
- Tailscale gets people to the site. Their **site account** then decides what they can do.

## 1. The customer's tailnet and its policy

Make a `sites.json` for the customer (start from `deploy/tailscale/sites.example.json`):

```json
{
  "customer": "Example Holdings",
  "owners": ["it@example.com"],
  "admins": ["facilities@example.com"],
  "supportPorts": [443, 22],
  "sites": [
    {"name": "plant1", "people": ["pat@example.com"]},
    {"name": "office", "people": ["sam@example.com", "pat@example.com"]}
  ]
}
```

- **owners:** may tag nodes. You, or the customer's IT, log in as one of them when a site server joins.
- **admins:** reach every site of this customer.
- **sites:** the BMS site names, and the Tailscale logins of the people who reach each.
- **supportPorts:** what shared-in support may reach (HTTPS and SSH by default).

Make the policy:

```bash
python3 deploy/tailscale/policy.py sites.json > policy.hujson
```

Paste it into the customer's Tailscale admin console under **Access controls**. It includes **tests** that prove each
person is refused the sites that are not theirs; Tailscale will not save a policy whose tests fail.

The policy **replaces** the tailnet's own. Use the customer's tailnet for the BMS only, or merge the rules by hand.

## 2. Join the site server

On the Proxmox host, as root:

```bash
deploy/proxmox/tailscale-host.sh --site plant1
```

It installs Tailscale, joins as `bms-plant1` with `tag:bms-site-plant1`, and serves HTTPS on port 443 to the web app
(`http://10.77.0.10:80` over the private bridge).
- Open the login URL it prints, as one of the tag owners.
- For an unattended install, give a one-off auth key in the environment only:
  `TS_AUTHKEY=tskey-… deploy/proxmox/tailscale-host.sh --site plant1`. The key is passed through a file, never a command
  line.
- `DRY_RUN=1` shows what it would do.

The site is then at `https://bms-plant1.<tailnet>.ts.net/`.

## 3. Share it with support

In the customer's admin console, open the machine `bms-plant1`, choose **Share**, and share it with the support
tailnet. Accept the share in the support tailnet. Shared-in users are `autogroup:shared` in the customer's policy.

## 4. What a person needs

- The Tailscale app on their phone or laptop, logged in to the customer's tailnet (the customer invites them).
- Their Tailscale login in the right group in `sites.json`.
- A site account (Setup → Users).

## Logins and the audit log

- `tailscale serve` passes the person's Tailscale identity on. The backend believes it only from the host
  (`BMS_TRUSTED_PROXIES=10.77.0.1`, set by the install).
- Each login says "logged in over Tailscale as name@example.com" in the audit log.
- A login to a **platform account** (support) is recorded as **support-login**, highlighted for the customer's admin.
- **Allowed networks** (Setup → Security) sees remote people at their tailnet address (`100.64.0.0/10`). Add that range if
  the list is used.

## Checks (bench test 12)

- The site opens over Tailscale from a phone and a laptop.
- A person in another site's group is refused (the policy tests check this too).
- The audit log shows the Tailscale identity on logins, and `support-login` for support.
