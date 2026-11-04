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


class Space(_Model):
    name: Name
    dis: str
    tags: Tags = {}


class Equip(_Model):
    name: Name
    dis: str
    space: Name | None = None
    parent: Name | None = None  # the "part of" link (main spec 5.2)
    tags: Tags = {}


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


Bus = Annotated[ModbusRtuBus | ModbusRtuOverTcpBus | ModbusTcpGatewayBus, Field(discriminator="type")]


class ModbusBusConnection(_Model):
    """A device on a bus: its unit (slave) ID and how many registers it reads at once."""

    bus: Name
    unit_id: int = Field(ge=1, le=247)
    block_size: int = Field(default=32, ge=1, le=125)


class Device(_Model):
    name: Name
    dis: str
    # modbus_tcp: a device with its own Modbus TCP connection.
    # modbus: a device on a bus (RS485 line or serial gateway) from `buses`.
    protocol: Literal["modbus_tcp", "modbus"]
    connection: ModbusTcpConnection | ModbusBusConnection
    enabled: bool = True

    @model_validator(mode="after")
    def _connection_matches_protocol(self) -> Device:
        wanted = ModbusTcpConnection if self.protocol == "modbus_tcp" else ModbusBusConnection
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
    address: ModbusAddress
    poll_class: PollClass = PollClass.NORMAL
    stale_after_s: float | None = Field(default=None, gt=0)
    his: bool = True


class SiteConfig(_Model):
    schema_: Literal["devine-bms-config/0"] = Field(alias="schema")
    site: Site
    spaces: list[Space] = []
    equips: list[Equip] = []
    feeds: list[Feed] = []
    buses: list[Bus] = []
    devices: list[Device] = []
    points: list[Point] = []


def json_schema() -> dict[str, Any]:
    """The published JSON Schema for the file format."""
    schema = SiteConfig.model_json_schema(by_alias=True)
    schema["$id"] = SCHEMA_ID
    schema["title"] = "Devine BMS site configuration"
    return schema
