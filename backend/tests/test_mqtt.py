"""MQTT field devices (milestone 4d): payload parsing, and a real Mosquitto broker per test."""

import asyncio
import copy
import json
import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path

import aiomqtt
import pytest

from devine_bms.config.model import Kind, MqttAddress, PollClass
from devine_bms.config.validate import ConfigError, parse_config
from devine_bms.drivers.mqtt.payload import PayloadError, point_value
from devine_bms.runtime import Runtime
from devine_bms.runtime_model import RuntimePoint
from devine_bms.storage.base import MemoryStorage
from devine_bms.values import Status
from helpers import TEST_INTERVALS, runtime_config_from
from simulators.mqtt_devices import SimMqttDevices

MOSQUITTO = shutil.which("mosquitto") or ("/usr/sbin/mosquitto" if Path("/usr/sbin/mosquitto").exists() else None)
needs_broker = pytest.mark.skipif(MOSQUITTO is None, reason="mosquitto is not installed")


# ---------- payload parsing ----------

def pt(kind, topic="t", json_path=None, enum=None):
    return RuntimePoint(id=uuid.uuid4(), name="p", device_id=uuid.uuid4(), kind=Kind(kind), unit=None,
                        enum=tuple(enum) if enum else None, address=MqttAddress(topic=topic, json_path=json_path),
                        poll_class=PollClass.FAST, stale_after_s=None, his=True)


def test_plain_numbers_and_words():
    assert point_value(pt("Number"), b"21.5") == 21.5
    assert point_value(pt("Number"), b" 7 \n") == 7.0
    assert point_value(pt("Bool"), b"ON") is True
    assert point_value(pt("Bool"), b"off") is False
    assert point_value(pt("Bool"), b"1") is True
    assert point_value(pt("Str", enum=["Cool", "Heat"]), b"heat") == "Heat"


def test_json_paths():
    payload = json.dumps({"ENERGY": {"Power": 412.5}, "channels": [{"v": 1}, {"v": 0}], "occupancy": False}).encode()
    assert point_value(pt("Number", json_path="ENERGY.Power"), payload) == 412.5
    assert point_value(pt("Bool", json_path="channels.1.v"), payload) is False
    assert point_value(pt("Bool", json_path="occupancy"), payload) is False
    assert point_value(pt("Number", json_path="occupancy"), payload) == 0.0


@pytest.mark.parametrize(("kind", "path", "payload", "words"), [
    ("Number", None, b"warm", "not a number"),
    ("Number", None, b"nan", "not a finite number"),
    ("Number", None, b"", "empty payload"),
    ("Bool", None, b"maybe", "not an on/off value"),
    ("Number", "ENERGY.Power", b"{}", "JSON has no"),
    ("Number", "a", b"not json", "not JSON"),
    ("Str", None, b"Dry", "not in the enum list"),
])
def test_unusable_payloads(kind, path, payload, words):
    with pytest.raises(PayloadError, match=words):
        point_value(pt(kind, json_path=path, enum=["Cool", "Heat"]), payload)


# ---------- a real broker ----------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Broker:
    def __init__(self, port: int | None = None, password: str | None = None) -> None:
        self.port = port or free_port()
        self.folder = Path(tempfile.mkdtemp(prefix="bms-mqtt-"))
        self.password = password
        self.proc = None

    async def start(self) -> None:
        conf = [f"listener {self.port} 127.0.0.1", "persistence false"]
        if os.geteuid() == 0:
            # As root, mosquitto drops to its own user, which cannot read this private temp folder.
            conf.append("user root")
        if self.password:
            pwfile = self.folder / "passwords"
            subprocess.run(["mosquitto_passwd", "-b", "-c", str(pwfile), "bms", self.password], check=True,
                           capture_output=True)
            conf += ["allow_anonymous false", f"password_file {pwfile}"]
        else:
            conf += ["allow_anonymous true"]
        (self.folder / "mosquitto.conf").write_text("\n".join(conf) + "\n")
        self.proc = subprocess.Popen([MOSQUITTO, "-c", str(self.folder / "mosquitto.conf")],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            await asyncio.sleep(0.05)
        raise RuntimeError("mosquitto did not start")

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(5)
            self.proc = None

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self.folder, ignore_errors=True)


@pytest.fixture
async def broker():
    b = Broker()
    await b.start()
    yield b
    b.cleanup()


def mqtt_point(name, topic, kind, json_path=None, device="plug1", equip="plug1", **extra):
    address = {"topic": topic}
    if json_path:
        address["jsonPath"] = json_path
    return {"name": f"site_{equip}_{name}", "dis": name, "equip": equip, "device": device, "type": "sensor",
            "kind": kind, "pollClass": "fast", "address": address, **extra}


def config(port: int, **broker_extra) -> dict:
    return {
        "schema": "devine-bms-config/0",
        "site": {"name": "bench", "dis": "Bench"},
        "equips": [
            {"name": "plug1", "dis": "Smart plug", "tags": {"elec": True, "meter": True}},
            {"name": "room1", "dis": "Room 1 sensor", "tags": {"zone": True}},
            {"name": "plant", "dis": "Plant", "tags": {"weatherStation": True}},
        ],
        "buses": [{"name": "mqtt1", "dis": "Site broker", "type": "mqtt_broker", "host": "127.0.0.1",
                   "port": port, **broker_extra}],
        "devices": [
            {"name": "plug1", "dis": "Tasmota plug", "protocol": "mqtt",
             "connection": {"broker": "mqtt1", "availabilityTopic": "tele/plug1/LWT",
                            "availabilityOnline": "Online", "availabilityOffline": "Offline"}},
            {"name": "room1", "dis": "Zigbee sensor", "protocol": "mqtt", "connection": {"broker": "mqtt1"}},
            {"name": "plain", "dis": "Plain topics", "protocol": "mqtt", "connection": {"broker": "mqtt1"}},
        ],
        "points": [
            mqtt_point("power", "tele/plug1/SENSOR", "Number", "ENERGY.Power", unit="W"),
            mqtt_point("energy", "tele/plug1/SENSOR", "Number", "ENERGY.Total", unit="kWh"),
            mqtt_point("relay", "stat/plug1/POWER", "Bool"),
            mqtt_point("temp", "zigbee2mqtt/room1_sensor", "Number", "temperature", device="room1", equip="room1"),
            mqtt_point("occupied", "zigbee2mqtt/room1_sensor", "Bool", "occupancy", device="room1", equip="room1"),
            mqtt_point("outside_temp", "site/plant/outside_temp", "Number", device="plain", equip="plant"),
        ],
    }


async def start(data):
    runtime = Runtime(MemoryStorage(runtime_config_from(data)), "bench", intervals=TEST_INTERVALS,
                      stale_check_s=0.05, flush_interval_s=0.1)
    await runtime.start()
    return runtime


def live(runtime, name):
    p = next(p for p in runtime.config.points if p.name.endswith(f"_{name}"))
    return runtime.cache.get(p.id)


async def wait_for(condition, timeout=6.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


ALL = ["power", "energy", "relay", "temp", "occupied", "outside_temp"]


def all_good(runtime, names=ALL):
    return all(live(runtime, n).status == Status.OK for n in names)


async def publish(port, topic, payload, retain=False):
    async with aiomqtt.Client("127.0.0.1", port, identifier=f"t-{uuid.uuid4().hex[:6]}") as c:
        await c.publish(topic, payload, qos=1, retain=retain)


@needs_broker
async def test_values_from_simulated_devices(broker):
    sim = SimMqttDevices(port=broker.port)
    runtime = await start(config(broker.port))
    try:
        await wait_for(lambda: runtime.buses[0].online is True)
        async with sim.client() as client:
            await sim.publish_all(client)
        await wait_for(lambda: all_good(runtime))
        assert live(runtime, "power").value == 412.5
        assert live(runtime, "energy").value == 123.456
        assert live(runtime, "relay").value is True
        assert live(runtime, "temp").value == 21.4 and live(runtime, "occupied").value is False
        assert live(runtime, "outside_temp").value == 18.5
        plug = next(s for s in runtime.pollers if s.device.name == "plug1")
        assert plug.online is True and plug.polls >= 3
    finally:
        await runtime.stop()


@needs_broker
async def test_retained_value_is_good_at_start(broker):
    await publish(broker.port, "site/plant/outside_temp", "17.0", retain=True)
    runtime = await start(config(broker.port))
    try:
        await wait_for(lambda: live(runtime, "outside_temp").status == Status.OK)
        assert live(runtime, "outside_temp").value == 17.0
    finally:
        await runtime.stop()


@needs_broker
async def test_bad_payload_faults_only_that_point(broker):
    runtime = await start(config(broker.port))
    try:
        await wait_for(lambda: runtime.buses[0].online is True)
        await publish(broker.port, "zigbee2mqtt/room1_sensor", json.dumps({"temperature": "n/a", "occupancy": True}))
        await wait_for(lambda: live(runtime, "temp").status == Status.FAULT)
        assert "not a number" in live(runtime, "temp").reason
        assert live(runtime, "occupied").status == Status.OK and live(runtime, "occupied").value is True
    finally:
        await runtime.stop()


@needs_broker
async def test_device_offline_then_back(broker):
    sim = SimMqttDevices(port=broker.port)
    runtime = await start(config(broker.port))
    try:
        await wait_for(lambda: runtime.buses[0].online is True)
        async with sim.client() as client:
            await sim.publish_all(client)
        await wait_for(lambda: all_good(runtime))
        await publish(broker.port, "tele/plug1/LWT", "Offline", retain=True)
        await wait_for(lambda: live(runtime, "power").status == Status.DOWN)
        assert "Offline" in live(runtime, "relay").reason
        assert live(runtime, "temp").status == Status.OK  # other devices are not affected
        async with sim.client() as client:
            await sim.publish_all(client)
        await wait_for(lambda: all_good(runtime))
    finally:
        await runtime.stop()


@needs_broker
async def test_broker_lost_and_back(broker):
    sim = SimMqttDevices(port=broker.port)
    runtime = await start(config(broker.port))
    try:
        await wait_for(lambda: runtime.buses[0].online is True)
        async with sim.client() as client:
            await sim.publish_all(client)
        await wait_for(lambda: all_good(runtime))
        broker.stop()
        await wait_for(lambda: all(live(runtime, n).status == Status.DOWN for n in ALL))
        assert "lost the MQTT broker" in live(runtime, "power").reason
        again = Broker(port=broker.port)
        await again.start()
        try:
            await wait_for(lambda: runtime.buses[0].online is True, timeout=10)
            async with sim.client() as client:
                await sim.publish_all(client)
            await wait_for(lambda: all_good(runtime))
        finally:
            again.cleanup()
    finally:
        await runtime.stop()


@needs_broker
async def test_stale_when_a_device_goes_quiet(broker):
    data = config(broker.port)
    data["points"][5]["staleAfterS"] = 0.3
    runtime = await start(data)
    try:
        await wait_for(lambda: runtime.buses[0].online is True)
        await publish(broker.port, "site/plant/outside_temp", "18.0")
        await wait_for(lambda: live(runtime, "outside_temp").status == Status.OK)
        await wait_for(lambda: live(runtime, "outside_temp").status == Status.STALE, timeout=3)
    finally:
        await runtime.stop()


@needs_broker
async def test_password_from_an_environment_variable(monkeypatch):
    if not shutil.which("mosquitto_passwd"):
        pytest.skip("mosquitto_passwd is not installed")
    secured = Broker(password="s3cret-pass")
    await secured.start()
    try:
        monkeypatch.setenv("BMS_TEST_MQTT_PASSWORD", "s3cret-pass")
        good = await start(config(secured.port, username="bms", passwordEnv="BMS_TEST_MQTT_PASSWORD"))
        try:
            await wait_for(lambda: good.buses[0].online is True)
        finally:
            await good.stop()
        monkeypatch.setenv("BMS_TEST_MQTT_PASSWORD", "wrong")
        bad = await start(config(secured.port, username="bms", passwordEnv="BMS_TEST_MQTT_PASSWORD"))
        try:
            await wait_for(lambda: bad.buses[0].online is False)
            await wait_for(lambda: live(bad, "power").status == Status.DOWN)
        finally:
            await bad.stop()
    finally:
        secured.cleanup()


# ---------- config checks ----------

def errors_of(data):
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    return str(exc.value)


def test_wildcards_are_not_point_topics():
    data = config(1883)
    data["points"][0]["address"]["topic"] = "tele/+/SENSOR"
    assert "wildcards" in errors_of(data)


def test_device_needs_an_existing_broker():
    data = config(1883)
    data["devices"][0]["connection"]["broker"] = "nope"
    assert "MQTT broker 'nope' does not exist" in errors_of(data)


def test_point_address_must_match_the_device():
    data = config(1883)
    data["points"][0]["address"] = {"objectType": "analogInput", "instance": 1}
    assert "is MQTT, so the point needs a MQTT address" in errors_of(data)


def test_true_and_false_texts_cannot_overlap():
    data = config(1883)
    data["points"][2]["address"]["trueValues"] = ["on"]
    data["points"][2]["address"]["falseValues"] = ["ON", "off"]
    assert "in both" in errors_of(data)


def test_password_is_never_in_the_file():
    data = config(1883, password="plain")
    with pytest.raises(ConfigError):
        parse_config(data)


def test_valid_config():
    config_, warnings = parse_config(copy.deepcopy(config(1883)))
    assert config_.buses[0].type == "mqtt_broker" and warnings == []


@needs_broker
async def test_listen_tool_lists_topics_and_json_paths(broker):
    from devine_bms.drivers.mqtt.tools import describe, listen

    sim = SimMqttDevices(port=broker.port)
    async with sim.client() as client:
        await sim.publish_all(client)  # LWT and outside_temp are retained, so a listener sees them
    task = asyncio.create_task(listen("127.0.0.1", broker.port, "#", 1.0))
    await asyncio.sleep(0.3)
    async with sim.client() as client:
        await sim.publish_all(client)
    lines = "\n".join(describe(await task))
    assert "tele/plug1/SENSOR  (JSON)" in lines
    assert "jsonPath ENERGY.Power" in lines
    assert "site/plant/outside_temp  = '18.5'" in lines
