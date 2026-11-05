"""MQTT bench tool (milestone 4d): list the topics a broker carries, with a sample of each payload.

Read-only: it subscribes and never publishes. It helps find the topics and JSON paths for
points before writing the site configuration (topic discovery in the web app is milestone 5).

    uv run python -m devine_bms.drivers.mqtt.tools listen --host 192.168.10.5 --seconds 30
    uv run python -m devine_bms.drivers.mqtt.tools listen --host 192.168.10.5 --topic 'tele/#' \\
        --username bms --password-env BMS_MQTT_PASSWORD
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

import aiomqtt


def json_paths(data, prefix: str = "") -> list[tuple[str, object]]:
    """Every leaf in a JSON value as (path, value), for example ('ENERGY.Power', 412.5)."""
    if isinstance(data, dict):
        out = []
        for key, value in data.items():
            out += json_paths(value, f"{prefix}{key}.")
        return out
    if isinstance(data, list):
        out = []
        for i, value in enumerate(data):
            out += json_paths(value, f"{prefix}{i}.")
        return out
    return [(prefix.rstrip("."), data)]


async def listen(host: str, port: int, topic: str, seconds: float, username: str | None = None,
                 password: str | None = None) -> dict[str, bytes]:
    seen: dict[str, bytes] = {}
    async with aiomqtt.Client(host, port, username=username, password=password,
                              identifier=f"devine-bms-listen-{uuid.uuid4().hex[:8]}") as client:
        await client.subscribe(topic)
        try:
            async with asyncio.timeout(seconds):
                async for message in client.messages:
                    seen[str(message.topic)] = bytes(message.payload or b"")
        except TimeoutError:
            pass
    return seen


def describe(seen: dict[str, bytes]) -> list[str]:
    lines = []
    for topic in sorted(seen):
        text = seen[topic].decode("utf-8", "replace").strip()
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, (dict, list)):
            lines.append(f"{topic}  (JSON)")
            for path, value in json_paths(data):
                lines.append(f"    jsonPath {path:30} = {value!r}")
        else:
            lines.append(f"{topic}  = {text[:80]!r}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MQTT bench tool (read-only)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("listen", help="list topics and sample payloads")
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--topic", default="#", help="topic filter, default everything (#)")
    p.add_argument("--seconds", type=float, default=15.0)
    p.add_argument("--username")
    p.add_argument("--password-env", help="environment variable that holds the password")
    args = parser.parse_args(argv)
    password = os.environ.get(args.password_env) if args.password_env else None
    try:
        seen = asyncio.run(listen(args.host, args.port, args.topic, args.seconds, args.username, password))
    except aiomqtt.MqttError as exc:
        print(f"Cannot use the broker at {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1
    if not seen:
        print(f"No messages in {args.seconds:g} s on '{args.topic}'.")
    for line in describe(seen):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
