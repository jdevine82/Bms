"""MQTT field devices through a broker (main spec 5.1, milestone 4d).

MQTT pushes values instead of being polled. One BrokerLink per broker subscribes to every
point's topic and every device's availability topic, and turns messages into values:
- a message gives the point its value, or Bad (fault) if the payload is not usable;
- an availability message equal to the device's offline text makes every point of that
  device Bad (down) until a new value arrives;
- losing the broker makes every point on it Bad (down); it reconnects with back-off;
- no message within a point's stale time makes it Stale (main spec 5.2.1). Devices that
  publish rarely need a matching `staleAfterS`.
Retained messages arrive when the BMS subscribes, so points with a retained value are
Good straight away. The BMS does not publish in this milestone (commands are milestone 7).
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import uuid

import aiomqtt

from devine_bms.config.model import MqttBroker
from devine_bms.drivers.mqtt.payload import PayloadError, point_value
from devine_bms.poller.quality import QualityTracker
from devine_bms.runtime_model import RuntimeDevice, RuntimePoint
from devine_bms.values import Status

log = logging.getLogger(__name__)

BACKOFF_START_S = 1.0
BACKOFF_MAX_S = 30.0


class MqttDeviceState:
    """Health of one MQTT device, reported like a polled device (messages count as polls)."""

    def __init__(self, device: RuntimeDevice, points: list[RuntimePoint]) -> None:
        self.device = device
        self.points = points
        self.online: bool | None = None
        self.polls = 0  # messages that gave a value
        self.failures = 0  # unusable payloads and offline reports


class BrokerLink:
    shared = True

    def __init__(self, settings: MqttBroker, devices: list[RuntimeDevice], points_for, tracker: QualityTracker,
                 site_name: str) -> None:
        self.settings = settings
        self.name = settings.name
        self.where = f"MQTT {settings.host}:{settings.port}"
        self.driver = self  # the health API reads `where` and `shared` from `driver`
        self.tracker = tracker
        self.site_name = site_name
        self.online: bool | None = None
        self.devices = [MqttDeviceState(d, points_for(d.id)) for d in devices]
        self.routes: dict[str, list[tuple[MqttDeviceState, RuntimePoint]]] = {}
        self.availability: dict[str, list[MqttDeviceState]] = {}
        for state in self.devices:
            for point in state.points:
                self.routes.setdefault(point.address.topic, []).append((state, point))
            topic = state.device.connection.availability_topic
            if topic:
                self.availability.setdefault(topic, []).append(state)

    def topics(self) -> list[str]:
        return sorted(set(self.routes) | set(self.availability))

    def _client(self) -> aiomqtt.Client:
        s = self.settings
        password = os.environ.get(s.password_env) if s.password_env else None
        if s.password_env and password is None:
            log.warning("MQTT broker %s: environment variable %s is not set", self.name, s.password_env)
        # A unique client ID per connection: two clients with one ID keep disconnecting each other.
        identifier = s.client_id or f"devine-bms-{self.site_name}-{self.name}-{uuid.uuid4().hex[:8]}"
        return aiomqtt.Client(
            s.host, s.port, username=s.username, password=password, identifier=identifier,
            keepalive=s.keepalive_s, tls_context=ssl.create_default_context() if s.tls else None,
        )

    async def run(self) -> None:
        delay = BACKOFF_START_S
        while True:
            try:
                async with self._client() as client:
                    for topic in self.topics():
                        await client.subscribe(topic, qos=1)
                    if self.online is not True:
                        log.info("MQTT broker %s connected; %d topics", self.name, len(self.topics()))
                    self.online = True
                    delay = BACKOFF_START_S
                    async for message in client.messages:
                        self.handle(str(message.topic), bytes(message.payload or b""))
            except aiomqtt.MqttError as exc:
                self._broker_down(f"lost the MQTT broker {self.settings.host}:{self.settings.port}: {exc}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, BACKOFF_MAX_S)

    def handle(self, topic: str, payload: bytes) -> None:
        for state in self.availability.get(topic, ()):
            text = payload.decode("utf-8", "replace").strip()
            conn = state.device.connection
            if text.lower() == conn.availability_offline.lower():
                if state.online is not False:
                    log.warning("MQTT device %s reports offline", state.device.name)
                state.online = False
                state.failures += 1
                for point in state.points:
                    self.tracker.bad(point, Status.DOWN, f"the device reports '{text}' on {topic}")
            elif text.lower() == conn.availability_online.lower():
                state.online = True
        for state, point in self.routes.get(topic, ()):
            try:
                value = point_value(point, payload)
            except PayloadError as exc:
                state.failures += 1
                self.tracker.bad(point, Status.FAULT, f"{exc} (topic {topic})")
                continue
            state.polls += 1
            if not state.device.connection.availability_topic:
                state.online = True
            self.tracker.good(point, value)

    def _broker_down(self, reason: str) -> None:
        if self.online is not False:
            log.warning("MQTT broker %s is down: %s", self.name, reason)
        self.online = False
        for state in self.devices:
            state.online = False
            for point in state.points:
                self.tracker.bad(point, Status.DOWN, reason)

    def close(self) -> None:
        pass
