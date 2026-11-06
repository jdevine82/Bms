"""Site configuration file format `devine-bms-config/0` (milestone 2 spec section 5).

This is the first version of the configuration export format (main spec 3.1).
Entities refer to each other by `name`, never by database ID. JSON keys are
camelCase, like Haystack.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from devine_bms.drivers.modbus.codec import ByteOrder, DataType, WordOrder

SCHEMA_ID = "devine-bms-config/0"

# Lowercase letters, digits and underscores, starting with a letter (main spec 5.2 and 15).
Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)]


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class PointType(StrEnum):
    SENSOR = "sensor"
    SP = "sp"
    CMD = "cmd"


class Kind(StrEnum):
    NUMBER = "Number"
    BOOL = "Bool"
    STR = "Str"


class PollClass(StrEnum):
    FAST = "fast"
    NORMAL = "normal"
    SLOW = "slow"


# Default intervals for each speed class (main spec 5.1.4; exact values proven on the bench).
POLL_INTERVAL_S = {PollClass.FAST: 5.0, PollClass.NORMAL: 30.0, PollClass.SLOW: 300.0}


class Medium(StrEnum):
    AIR = "air"
    CHILLED_WATER = "chilledWater"
    HOT_WATER = "hotWater"
    CONDENSER_WATER = "condenserWater"
    ELEC = "elec"
    REFRIG = "refrig"


class ObjectType(StrEnum):
    COIL = "coil"
    DISCRETE_INPUT = "discreteInput"
    HOLDING_REGISTER = "holdingRegister"
    INPUT_REGISTER = "inputRegister"


# A tag value: True for a marker, or a string or number value.
TagValue = bool | str | int | float
Tags = dict[Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9_]*$")], TagValue]


class GeoCoord(_Model):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Site(_Model):
    name: Name
    dis: str
    tz: str = "Sydney"
    geo_coord: GeoCoord | None = None
    area_m2: float | None = None
    tags: Tags = {}


class TemplateLink(_Model):
    """Equipment (or a room) made from an equipment template (main spec 5.1.2, milestone 5A).

    `bindings` names the point that fills each role. Overrides are not stored: a point field
    that differs from what the template's role gives it is an override, and a template update
    leaves it alone (an override always wins).
    """

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: str
    bindings: dict[Name, Name] = {}
    parameters: dict[str, str | int | float | bool] = {}


class Space(_Model):
    name: Name
    dis: str
    tags: Tags = {}
    template: TemplateLink | None = None


class Equip(_Model):
    name: Name
    dis: str
    space: Name | None = None
    parent: Name | None = None  # the "part of" link (main spec 5.2)
    tags: Tags = {}
    template: TemplateLink | None = None


class Feed(_Model):
    from_: Name = Field(alias="from")
    to: Name
    medium: Medium


class ModbusTcpConnection(_Model):
    host: str
    port: int = Field(default=502, ge=1, le=65535)
    unit_id: int = Field(default=1, ge=0, le=255)
    timeout_s: float = Field(default=3.0, gt=0, le=60)
    retries: int = Field(default=2, ge=0, le=10)
    block_size: int = Field(default=32, ge=1, le=125)


class _BusBase(_Model):
    name: Name
    dis: str
    timeout_s: float = Field(default=1.0, gt=0, le=60)
    retries: int = Field(default=1, ge=0, le=10)
    enabled: bool = True


class ModbusRtuBus(_BusBase):
    """An RS485 line on a serial port (main spec 5.1). One adapter and port per bus, never shared."""

    type: Literal["modbus_rtu"]
    # A stable device name (udev rule by serial number, main spec 11), for example
    # /dev/serial/by-id/usb-FTDI_..., or a pyserial URL such as socket://host:port for tests.
    port: str = Field(min_length=1)
    baudrate: int = Field(default=9600, ge=1200, le=115200)
    parity: Literal["N", "E", "O"] = "N"
    data_bits: Literal[7, 8] = 8
    stop_bits: Literal[1, 2] = 1


class ModbusRtuOverTcpBus(_BusBase):
    """A serial-to-Ethernet gateway that passes raw RTU frames over TCP (main spec 5.1)."""

    type: Literal["modbus_rtu_over_tcp"]
    host: str
    port: int = Field(default=502, ge=1, le=65535)


class ModbusTcpGatewayBus(_BusBase):
    """A serial-to-Ethernet gateway that converts Modbus TCP to RTU, addressing devices by unit ID."""

    type: Literal["modbus_tcp_gateway"]
    host: str
    port: int = Field(default=502, ge=1, le=65535)


class BacnetMstpTrunk(_BusBase):
    """A BACnet MS/TP trunk, run by the bms-mstp service on the host (main spec 5.1, 9, 11; milestone 4c).

    The service owns the serial port and is the MS/TP master node; the backend reads devices
    through its local API. The port and MS/TP settings here must match the service's settings
    (`mstp/` in the repository); the commissioning check compares them.
    """

    type: Literal["bacnet_mstp"]
    # bms-mstp API address, for example 127.0.0.1:47900 or the host's address on a
    # host-only bridge when the backend runs in a container.
    service: str = Field(pattern=r"^[0-9.]+:[0-9]{1,5}$")
    port: str = Field(min_length=1, description="Serial port the service uses")
    baudrate: Literal[9600, 19200, 38400, 57600, 76800, 115200] = 38400
    # MAC 0 is the site server and MAC 1 the Pi HMI on every trunk (main spec 9).
    mac: int = Field(default=0, ge=0, le=1)
    max_master: int = Field(default=127, ge=1, le=127)
    max_info_frames: int = Field(default=1, ge=1, le=255)
    device_instance: int = Field(ge=0, le=4194302, description="The service node's own instance")
    timeout_s: float = Field(default=8.0, gt=0, le=120)  # longer than the service's own timeout x tries
    # This trunk's BACnet network number (milestone 8), unique on the site; suggested 101, 102, …
    network_number: int | None = Field(default=None, ge=1, le=65534)
    router: MstpRouter | None = None


class MstpRouter(_Model):
    """bms-mstp routes this trunk to BACnet/IP (milestone 8): its BACnet/IP side uses `interface`,
    a labelled address on the device-network port such as eth1:mstp1 with `address`, port 47808.
    The same settings go in the service's env file (BMS_MSTP_ROUTE_ARGS)."""

    enabled: bool = True
    interface: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,15}$")
    address: str = Field(pattern=r"^[0-9]{1,3}(\.[0-9]{1,3}){3}$")


class MqttBroker(_BusBase):
    """An MQTT broker that field devices publish to (main spec 5.1, milestone 4d).

    Usually the site's own Mosquitto (the bms-mqtt container, main spec 11). The password is
    never in the config file: `password_env` names an environment variable that holds it.
    """

    type: Literal["mqtt_broker"]
    host: str
    port: int = Field(default=1883, ge=1, le=65535)
    username: str | None = None
    password_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    tls: bool = False
    client_id: str | None = Field(default=None, max_length=64)
    keepalive_s: int = Field(default=30, ge=5, le=600)


Bus = Annotated[
    ModbusRtuBus | ModbusRtuOverTcpBus | ModbusTcpGatewayBus | BacnetMstpTrunk | MqttBroker,
    Field(discriminator="type"),
]


class BacnetIpConnection(_Model):
    """A BACnet/IP device (main spec 5.1, milestone 4b)."""

    # IP address, with the UDP port when it is not 47808, for example 192.168.10.20 or 192.168.10.20:47809.
    address: str = Field(pattern=r"^[0-9.]+(:[0-9]{1,5})?$")
    device_instance: int = Field(ge=0, le=4194302)
    timeout_s: float = Field(default=3.0, gt=0, le=60)
    retries: int = Field(default=1, ge=0, le=10)
    # Properties per ReadPropertyMultiple request. Each point reads two (value and status flags).
    block_size: int = Field(default=20, ge=1, le=100)
    # Subscribe to change-of-value for Fast and Normal points; they are then polled at the
    # Slow interval only, as a back-up in case a notification is missed (main spec 5.1.4).
    cov: bool = False
    cov_lifetime_s: int = Field(default=300, ge=60, le=86400)


class BacnetMstpConnection(_Model):
    """A BACnet MS/TP device on a trunk (milestone 4c)."""

    trunk: Name
    # MAC 0 and 1 are reserved for the site server and the Pi HMI (main spec 9).
    mac: int = Field(ge=2, le=127)
    device_instance: int = Field(ge=0, le=4194302)
    # Properties per ReadPropertyMultiple request (each point reads two). MS/TP frames are
    # at most 501 bytes, so keep requests small.
    block_size: int = Field(default=10, ge=1, le=50)


class MqttConnection(_Model):
    """An MQTT device: the broker it publishes to, and its optional availability topic.

    When the availability topic carries `availability_offline` (for example Tasmota's LWT
    "Offline"), every point of the device is Bad (down) until a new value arrives.
    """

    broker: Name
    availability_topic: str | None = Field(default=None, min_length=1)
    availability_online: str = "online"
    availability_offline: str = "offline"


class ModbusBusConnection(_Model):
    """A device on a bus: its unit (slave) ID and how many registers it reads at once."""

    bus: Name
    unit_id: int = Field(ge=1, le=247)
    block_size: int = Field(default=32, ge=1, le=125)


class WriteLimits(_Model):
    """The range the server accepts for a command to a Number point (main spec 5.6), for every role."""

    min: float
    max: float

    @model_validator(mode="after")
    def _ordered(self) -> WriteLimits:
        if self.min >= self.max:
            raise ValueError("write limits need min below max")
        return self


class OccupancyMap(_Model):
    """What a writable point is set to when its equipment's schedule is Occupied or Unoccupied
    (milestone 7B), for example On and Off, or 22 and 26 °C."""

    occupied: float | bool | str
    unoccupied: float | bool | str


class AlarmLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class PointAlarm(_Model):
    """An alarm on a point (main spec 5.5): a high or low limit, a state, or stale data.

    high: raised when the value is above `limit`; it returns to normal below limit - deadband.
    low: raised below `limit`; returns above limit + deadband.
    state: raised while a Bool or Str point equals `state`.
    stale: raised while the point's value is Stale.
    Only Good values raise or clear limit and state alarms (main spec 5.2.1).
    """

    type: Literal["high", "low", "state", "stale"]
    level: AlarmLevel
    limit: float | None = None
    deadband: float = Field(default=0.0, ge=0)
    state: bool | str | None = None
    delay_s: float = Field(default=0.0, ge=0, le=86400)  # the condition must hold this long
    message: str | None = Field(default=None, max_length=200)  # no sensitive detail (5.5)

    @model_validator(mode="after")
    def _fields_match_type(self) -> PointAlarm:
        if self.type in ("high", "low") and self.limit is None:
            raise ValueError(f"a {self.type} alarm needs a limit")
        if self.type == "state" and self.state is None:
            raise ValueError("a state alarm needs the state that raises it")
        if self.type not in ("high", "low") and (self.limit is not None or self.deadband):
            raise ValueError(f"a {self.type} alarm has no limit or deadband")
        if self.type != "state" and self.state is not None:
            raise ValueError(f"a {self.type} alarm has no state")
        return self

    @property
    def key(self) -> str:
        """Which alarm of the point this is: one per type, and one per state for state alarms."""
        return f"{self.type}:{self.state}" if self.type == "state" else self.type


class OfflineAlarm(_Model):
    """An alarm when a device stops answering (main spec 5.5). Only on devices where it is set."""

    level: AlarmLevel = AlarmLevel.WARNING
    delay_s: float = Field(default=300.0, ge=0, le=86400)
    message: str | None = Field(default=None, max_length=200)


class Device(_Model):
    name: Name
    dis: str
    # modbus_tcp: a device with its own Modbus TCP connection.
    # modbus: a device on a bus (RS485 line or serial gateway) from `buses`.
    # bacnet_ip: a BACnet/IP device.
    # bacnet_mstp: a BACnet MS/TP device on a trunk from `buses`.
    # mqtt: a device that publishes to an MQTT broker from `buses`.
    protocol: Literal["modbus_tcp", "modbus", "bacnet_ip", "bacnet_mstp", "mqtt"]
    connection: (
        ModbusTcpConnection | ModbusBusConnection | BacnetIpConnection | BacnetMstpConnection | MqttConnection
    )
    enabled: bool = True
    offline_alarm: OfflineAlarm | None = None

    @model_validator(mode="after")
    def _connection_matches_protocol(self) -> Device:
        wanted = {
            "modbus_tcp": ModbusTcpConnection,
            "modbus": ModbusBusConnection,
            "bacnet_ip": BacnetIpConnection,
            "bacnet_mstp": BacnetMstpConnection,
            "mqtt": MqttConnection,
        }[self.protocol]
        if not isinstance(self.connection, wanted):
            raise ValueError(f"protocol '{self.protocol}' needs a {wanted.__name__} connection")
        return self


class ModbusAddress(_Model):
    object_type: ObjectType
    address: int = Field(ge=0, le=65535, description="Wire address (0-based)")
    data_type: DataType
    byte_order: ByteOrder = ByteOrder.BIG
    word_order: WordOrder = WordOrder.HIGH_FIRST
    scale: float = 1.0
    offset: float = 0.0


class BacnetObjectType(StrEnum):
    ANALOG_INPUT = "analogInput"
    ANALOG_OUTPUT = "analogOutput"
    ANALOG_VALUE = "analogValue"
    BINARY_INPUT = "binaryInput"
    BINARY_OUTPUT = "binaryOutput"
    BINARY_VALUE = "binaryValue"
    MULTI_STATE_INPUT = "multiStateInput"
    MULTI_STATE_OUTPUT = "multiStateOutput"
    MULTI_STATE_VALUE = "multiStateValue"


class BacnetAddress(_Model):
    """A BACnet object and property. Multi-state values are 1-based: state 1 is enum[0]."""

    object_type: BacnetObjectType
    instance: int = Field(ge=0, le=4194302)
    property: Literal["presentValue"] = "presentValue"


class MqttAddress(_Model):
    """Where an MQTT point's value comes from (milestone 4d).

    Plain: the whole payload is the value, for example `21.5`, `ON` or `true`.
    JSON: `json_path` picks a field with dots between names, for example `ENERGY.Power`
    (Tasmota), `temperature` (Zigbee2MQTT) or `apower` (Shelly Gen2). A number in the path
    picks a list entry, for example `channels.0.value`.
    """

    topic: str = Field(min_length=1, max_length=512)
    json_path: str | None = Field(default=None, pattern=r"^[^.\s]+(\.[^.\s]+)*$")
    # Payload texts for true and false (Bool points), compared without case.
    true_values: list[str] = ["on", "true", "1", "yes", "online", "open", "active"]
    false_values: list[str] = ["off", "false", "0", "no", "offline", "closed", "inactive"]
    # Commands (milestone 7A): where a command is published, and what it looks like. In the
    # template, {value} is replaced by the value (the true or false text for Bool points), for
    # example {"state": "{value}"} for Zigbee2MQTT. Without a template the value alone is sent.
    command_topic: str | None = Field(default=None, min_length=1, max_length=512)
    command_template: str | None = Field(default=None, max_length=1000)
    command_true: str = "ON"
    command_false: str = "OFF"


class BacnetSettings(_Model):
    """The BMS's own BACnet/IP identity on the device network (main spec 5.1, 5.11)."""

    # The BMS's address with its network size, so broadcasts (Who-Is) reach the right subnet,
    # for example 192.168.10.5/24. 0.0.0.0/0 binds every interface.
    local_address: str = Field(pattern=r"^[0-9.]+/[0-9]{1,2}$")
    port: int = Field(default=47808, ge=1, le=65535)
    # Unique on the BACnet network. Every site must pick its own.
    device_instance: int = Field(ge=0, le=4194302)
    device_name: str = "Devine BMS"
    # The BACnet/IP network's number on this site (milestone 8), used by the MS/TP routers.
    network_number: int = Field(default=1, ge=1, le=65534)


class Point(_Model):
    name: Name
    dis: str
    equip: Name | None = None
    space: Name | None = None
    device: Name
    type: PointType
    kind: Kind
    unit: str | None = None
    enum: list[str] | None = None
    tags: Tags = {}
    address: ModbusAddress | BacnetAddress | MqttAddress
    poll_class: PollClass = PollClass.NORMAL
    stale_after_s: float | None = Field(default=None, gt=0)
    his: bool = True
    alarms: list[PointAlarm] = []
    write_limits: WriteLimits | None = None  # milestone 7A
    occupancy: OccupancyMap | None = None  # milestone 7B


class TemplateRole(_Model):
    """A named point an equipment template expects (main spec 5.1.2), with the point fields it gives."""

    role: Name
    dis: str
    type: PointType
    kind: Kind
    unit: str | None = None
    enum: list[str] | None = None
    tags: Tags = {}
    required: bool = True
    poll_class: PollClass = PollClass.NORMAL
    stale_after_s: float | None = Field(default=None, gt=0)
    his: bool = True
    alarms: list[PointAlarm] = []  # default alarms for the points made for this role
    write_limits: WriteLimits | None = None
    occupancy: OccupancyMap | None = None


class EquipTemplate(_Model):
    """An equipment or room template (main spec 5.1.2). Each version is a separate entry."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: str
    dis: str
    applies_to: Literal["equip", "space"] = "equip"
    tags: Tags = {}  # tags every instance gets, for example {"fcu": true}
    roles: list[TemplateRole]
    parameters: dict[str, str | int | float | bool] = {}  # defaults, for example a setpoint range
    graphic: str | None = None  # a graphic reference, once graphics exist (milestone 9)
    logic: str | None = None  # a logic reference, once logic exists (milestone 10)
    notes: list[str] = []


# ---------- graphics (milestone 9A) ----------

ItemId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=60)]
# Named colours, so each theme (and the HMI) picks the shade.
Colour = Literal["grey", "blue", "cyan", "green", "amber", "red"]
XY = tuple[float, float]


class ValueBand(_Model):
    """One band of a value-band rule: values below `below` (the last band has none) get `colour`."""

    below: float | None = None
    colour: Colour
    flow: bool = True  # a pipe or duct in this band shows flow


class FollowsRule(_Model):
    """A pipe or duct coloured like a symbol: On shows flow, Off is grey."""

    type: Literal["follows"]
    item: ItemId
    colour: Colour = "green"


class BandsRule(_Model):
    type: Literal["bands"]
    point: Name
    bands: list[ValueBand] = Field(min_length=1, max_length=4)
    hysteresis: float = Field(default=0, ge=0)


class FillRule(_Model):
    """Proportional fill: 0 to 100 % of the point's value (or of min..max)."""

    type: Literal["fill"]
    point: Name
    colour: Colour = "blue"
    min: float = 0
    max: float = 100


ColourRule = Annotated[FollowsRule | BandsRule | FillRule, Field(discriminator="type")]


class SymbolBindings(_Model):
    run: Name | None = None
    value: Name | None = None
    fault: Name | None = None
    command: Name | None = None


class SymbolItem(_Model):
    type: Literal["symbol"]
    id: ItemId
    symbol: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    x: float
    y: float
    size: float = Field(default=64, gt=0, le=2000)
    rotate: Literal[0, 90, 180, 270] = 0
    label: str | None = None
    bind: SymbolBindings = SymbolBindings()


class ValueItem(_Model):
    type: Literal["value"]
    id: ItemId
    x: float
    y: float
    point: Name
    label: str | None = None
    decimals: int | None = Field(default=None, ge=0, le=6)


class LineItem(_Model):
    type: Literal["pipe", "duct"]
    id: ItemId
    path: list[XY] = Field(min_length=2, max_length=200)
    width: float | None = Field(default=None, gt=0, le=100)
    rule: ColourRule | None = None


class RoomItem(_Model):
    type: Literal["room"]
    id: ItemId
    space: Name
    x: float
    y: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)
    values: list[Name] = Field(default=[], max_length=4)
    rule: BandsRule | None = None


class TextItem(_Model):
    type: Literal["text"]
    id: ItemId
    x: float
    y: float
    text: str = Field(max_length=200)
    size: float = Field(default=16, gt=0, le=200)


GraphicItem = Annotated[SymbolItem | ValueItem | LineItem | RoomItem | TextItem, Field(discriminator="type")]


class GraphicBackground(_Model):
    """An imported SVG drawn under everything else (milestone 9B), for example a floor plan."""

    svg: str = Field(max_length=1024 * 1024)
    x: float = 0
    y: float = 0
    w: float = Field(gt=0)
    h: float = Field(gt=0)
    opacity: float = Field(default=1, ge=0.05, le=1)


class SymbolShape(_Model):
    """A shape of a symbol drawn in the editor, on the 64 x 64 grid (milestone 9B)."""

    type: Literal["line", "polyline", "rect", "circle", "text"]
    layer: Literal["off", "on", "both"] = "both"
    points: list[XY] = Field(default=[], max_length=200)  # line and polyline
    x: float = 0
    y: float = 0
    w: float = 0
    h: float = 0
    r: float = 0
    text: str = Field(default="", max_length=40)
    size: float = Field(default=12, gt=0, le=64)
    fill: bool = False


class CustomSymbol(_Model):
    """A site's own symbol (main spec 6.4, milestone 9B): drawn in the editor or imported.

    `svg` is cleaned (graphics/sanitize.py) and has `state-off` and `state-on` groups."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=60)
    dis: str
    category: str = "Site symbols"
    kind: Literal["state", "position", "level", "value"] = "state"
    animation: Literal["none", "spin", "flash"] = "none"  # for the On group
    svg: str = Field(max_length=64 * 1024)
    shapes: list[SymbolShape] | None = Field(default=None, max_length=300)  # drawn ones, to edit again


class GraphicTab(_Model):
    order: int = Field(ge=0, le=10000)


class Graphic(_Model):
    """A live graphic (main spec 6.4): a drawing area in its own units with bound items."""

    name: Name
    dis: str
    kind: Literal["site", "room", "system"]
    space: Name | None = None
    width: float = Field(default=1000, gt=0, le=20000)
    height: float = Field(default=600, gt=0, le=20000)
    tab: GraphicTab | None = None
    background: GraphicBackground | None = None  # milestone 9B
    items: list[GraphicItem] = Field(default=[], max_length=2000)


class SiteConfig(_Model):
    schema_: Literal["devine-bms-config/0"] = Field(alias="schema")
    site: Site
    # The site's own equipment templates (every saved version). Built-in ones ship with the software.
    equip_templates: list[EquipTemplate] = []
    spaces: list[Space] = []
    equips: list[Equip] = []
    feeds: list[Feed] = []
    bacnet: BacnetSettings | None = None
    buses: list[Bus] = []
    devices: list[Device] = []
    points: list[Point] = []
    graphics: list[Graphic] = []  # milestone 9
    symbols: list[CustomSymbol] = []  # the site's own symbols (milestone 9B)


def json_schema() -> dict[str, Any]:
    """The published JSON Schema for the file format."""
    schema = SiteConfig.model_json_schema(by_alias=True)
    schema["$id"] = SCHEMA_ID
    schema["title"] = "Devine BMS site configuration"
    return schema
