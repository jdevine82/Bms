#!/usr/bin/env python3
"""Write a customer tailnet's access policy (milestone 12a, decision 030).

One tailnet per customer. Each of the customer's site servers is tagged tag:bms-site-<site>
(deploy/proxmox/tailscale-host.sh). This makes the policy file for that tailnet:

- each site's people reach only their own site, on HTTPS (443);
- the customer's admins reach all of the customer's sites on 443;
- support (the site servers are shared into the support tailnet, so support users are
  autogroup:shared here) reaches each site on the support ports (443 and SSH by default);
- policy tests that prove every person is blocked from the sites that are not theirs.
  Tailscale refuses to save a policy whose tests fail.

Usage: python3 policy.py sites.json > policy.hujson    (then paste it into the admin console,
Access controls, or push it with the Tailscale API). Standard library only.

sites.json:
  {"customer": "acme",
   "owners": ["it@acme.example"],                  # may tag nodes (tagOwners); usually the installer too
   "admins": ["facilities@acme.example"],          # reach every site of this customer
   "supportPorts": [443, 22],                      # optional
   "sites": [{"name": "plant1", "people": ["pat@acme.example"]},
             {"name": "office", "people": ["sam@acme.example"]}]}
"""

from __future__ import annotations

import json
import re
import sys

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PolicyError(ValueError):
    pass


def tag(site: str) -> str:
    return f"tag:bms-site-{site}"


def build(spec: dict) -> dict:
    sites = spec.get("sites") or []
    if not sites:
        raise PolicyError("list at least one site")
    owners = list(spec.get("owners") or [])
    admins = list(spec.get("admins") or [])
    ports = [int(p) for p in (spec.get("supportPorts") or [443, 22])]
    if not owners:
        raise PolicyError("owners: who may tag the site servers (at least one email)")
    names = []
    for s in sites:
        name = str(s.get("name", ""))
        if not NAME.match(name):
            raise PolicyError(f"site name {name!r}: lower-case letters, digits and -, as in the BMS site name")
        if name in names:
            raise PolicyError(f"site {name} is listed twice")
        names.append(name)
    for email in owners + admins + [p for s in sites for p in s.get("people") or []]:
        if not EMAIL.match(str(email)):
            raise PolicyError(f"{email!r} is not an email address (a Tailscale login)")
    if not all(1 <= p <= 65535 for p in ports):
        raise PolicyError("supportPorts: port numbers")

    groups = {f"group:site-{s['name']}": sorted(set(s.get("people") or [])) for s in sites}
    if admins:
        groups["group:bms-admins"] = sorted(set(admins))
    acls = [{"action": "accept", "src": [f"group:site-{s['name']}"], "dst": [f"{tag(s['name'])}:443"]}
            for s in sites if s.get("people")]
    if admins:
        acls.append({"action": "accept", "src": ["group:bms-admins"], "dst": [f"{tag(n)}:443" for n in names]})
    acls.append({"action": "accept", "src": ["autogroup:shared"],
                 "dst": [f"{tag(n)}:{','.join(str(p) for p in ports)}" for n in names]})
    tests = []
    for s in sites:
        others = [f"{tag(n)}:443" for n in names if n != s["name"]]
        for person in sorted(set(s.get("people") or [])):
            if person in admins:
                continue
            test = {"src": person, "accept": [f"{tag(s['name'])}:443"]}
            # Only the sites this person is not listed for anywhere else are refused.
            mine = {x["name"] for x in sites if person in (x.get("people") or [])}
            deny = [d for d in others if d.split(":")[1].removeprefix("bms-site-") not in mine]
            deny += [f"{tag(s['name'])}:22"]
            test["deny"] = deny
            tests.append(test)
    for a in sorted(set(admins)):
        tests.append({"src": a, "accept": [f"{tag(n)}:443" for n in names], "deny": [f"{tag(names[0])}:22"]})
    return {"tagOwners": {tag(n): sorted(set(owners)) for n in names}, "groups": groups, "acls": acls,
            "tests": tests}


def render(spec: dict) -> str:
    customer = spec.get("customer", "the customer")
    head = (f"// Devine BMS access policy for {customer}'s tailnet (deploy/tailscale/policy.py).\n"
            "// Generated: edit sites.json and run the script again rather than editing this file.\n")
    return head + json.dumps(build(spec), indent=2) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            spec = json.load(f)
        sys.stdout.write(render(spec))
    except (OSError, ValueError) as exc:
        print(f"policy.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
