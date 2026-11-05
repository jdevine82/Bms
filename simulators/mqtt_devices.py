"""Simulated MQTT devices for development and tests (milestone 4d).

Publishes like common real devices:

    Tasmota smart plug "plug1"
        tele/plug1/LWT      Online / Offline (retained, the availability topic)
        tele/plug1/SENSOR   {"ENERGY": {"Power": 412.5, "Total": 123.456, "Voltage": 240}}
        stat/plug1/POWER    ON / OFF
    Zigbee2MQTT room sensor
        zigbee2mqtt/room1_sensor   {"temperature": 21.4, "humidity": 52, "occupancy": false}
    A plain topic (retained)
        site/plant/outside_temp    18.5

Run (needs a broker, for example mosquitto on localhost):
    python simulators/mqtt_devices.py --host 127.0.0.1 --port 1883
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time

import aiomqtt


class SimMqttDevices:
    def __init__(self, host: str = "127.0.0.1", port: int = 1883) -> None:
        self.host = host
        self.port = port
        self.power_w = 412.5
        self.energy_kwh = 123.456
        self.relay_on = True
        self.room_temp = 21.4
        self.humidity = 52
        self.occupancy = False
        self.outside_temp = 18.5

    def client(self) -> aiomqtt.Client:
        return aiomqtt.Client(
            self.host, self.port, identifier=f"sim-{time.monotonic_ns()}",
            will=aiomqtt.Will("tele/plug1/LWT", "Offline", qos=1, retain=True),
        )

    async def publish_all(self, client: aiomqtt.Client) -> None:
        await client.publish("tele/plug1/LWT", "Online", qos=1, retain=True)
        await client.publish("tele/plug1/SENSOR", json.dumps(
            {"Time": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "ENERGY": {"Power": self.power_w, "Total": self.energy_kwh, "Voltage": 240}}), qos=1)
        await client.publish("stat/plug1/POWER", "ON" if self.relay_on else "OFF", qos=1)
        await client.publish("zigbee2mqtt/room1_sensor", json.dumps(
            {"temperature": self.room_temp, "humidity": self.humidity, "occupancy": self.occupancy}), qos=1)
        await client.publish("site/plant/outside_temp", f"{self.outside_temp}", qos=1, retain=True)

    def step(self) -> None:
        phase = (time.time() % 120.0) / 120.0 * 2 * math.pi
        self.power_w = round(400 + 50 * math.sin(phase), 1)
        self.energy_kwh = round(self.energy_kwh + self.power_w / 3600 / 1000 * 5, 4)
        self.room_temp = round(21.4 + 0.8 * math.sin(phase), 1)

    async def run(self, interval_s: float = 5.0) -> None:
        async with self.client() as client:
            while True:
                self.step()
                await self.publish_all(client)
                await asyncio.sleep(interval_s)


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Simulated MQTT devices")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    await SimMqttDevices(args.host, args.port).run(args.interval)


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
