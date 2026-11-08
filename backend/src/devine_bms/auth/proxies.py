"""Trusted proxies (milestone 12a): loopback, and on a site server the Proxmox host on the private
bridge, where tailscale serve runs. Their X-Forwarded-For names the client; a request from one of
them without X-Forwarded-For is from the server itself (for example an SSH tunnel through the host
for the first-run page)."""

from __future__ import annotations

import ipaddress

_TRUSTED: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []


def set_trusted_proxies(text: str) -> None:
    """BMS_TRUSTED_PROXIES: addresses or networks, comma-separated (for example 10.77.0.1)."""
    nets = []
    for item in (text or "").split(","):
        item = item.strip()
        if item:
            nets.append(ipaddress.ip_network(item, strict=False))
    _TRUSTED[:] = nets


def trusted_proxy(address: str | None) -> bool:
    """Loopback, or one of BMS_TRUSTED_PROXIES: in both cases, this server."""
    try:
        addr = ipaddress.ip_address(address or "")
    except ValueError:
        return address == "localhost"
    return addr.is_loopback or any(addr in net for net in _TRUSTED)
