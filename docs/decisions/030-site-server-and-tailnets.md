# 030: Site server layout and Tailscale tailnets (milestone 12a)

Date: 7 Nov 2026. Status: decided (1 to 4); proposed (5).

1. Milestone 12 has two parts: 12a (packaging, Tailscale, policy) and 12b (remote updates with rollback).
2. **One tailnet per customer.** A customer's people can only ever reach that customer's site servers. Within a customer, one tag per site and a generated policy (with tests) decide who reaches which site.
3. **Support by node sharing:** each site server is shared from the customer's tailnet into the support tailnet. One place for support; the customer can see and revoke the share.
4. **Two LXC containers** (`bms-app`: backend, web app, Mosquitto; `bms-db`: PostgreSQL 16 + TimescaleDB) **and Tailscale on the Proxmox host**, with `tailscale serve` for HTTPS. This replaces the five-container layout in main spec 11: fewer things to update and roll back, and a private bridge keeps the database off every other network.
5. Proposed defaults: Debian 13 containers, bridge `vmbrbms` 10.77.0.0/24, interactive Tailscale login (see `docs/specs/m12a-site-server-remote.md`).

Consequences: the backend trusts proxy headers only from configured addresses (`BMS_TRUSTED_PROXIES`); Tailscale ACLs are written per customer, not once for all sites.
