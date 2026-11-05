"""Turn an MQTT payload into a point value (milestone 4d).

Raises PayloadError when the payload does not give a usable value; the point is then
Bad (fault) with the reason, and the other points are unaffected.
"""

from __future__ import annotations

import json
import math
from typing import Any

from devine_bms.config.model import Kind, MqttAddress
from devine_bms.runtime_model import RuntimePoint


class PayloadError(ValueError):
    pass


def extract(address: MqttAddress, payload: bytes) -> Any:
    """The raw value: the whole payload, or the field at `json_path`."""
    try:
        text = payload.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise PayloadError("payload is not UTF-8 text") from exc
    if address.json_path is None:
        if text == "":
            raise PayloadError("empty payload")
        return text
    try:
        data: Any = json.loads(text)
    except ValueError as exc:
        raise PayloadError(f"payload is not JSON: {text[:60]!r}") from exc
    for part in address.json_path.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            raise PayloadError(f"JSON has no '{address.json_path}'")
    return data


def convert(point: RuntimePoint, raw: Any) -> float | bool | str:
    address: MqttAddress = point.address
    if point.kind == Kind.NUMBER:
        if isinstance(raw, bool):
            return 1.0 if raw else 0.0
        if isinstance(raw, (int, float)):
            number = float(raw)
        elif isinstance(raw, str):
            try:
                number = float(raw.strip())
            except ValueError as exc:
                raise PayloadError(f"not a number: {raw[:40]!r}") from exc
        else:
            raise PayloadError(f"not a number: {type(raw).__name__}")
        if not math.isfinite(number):
            raise PayloadError(f"not a finite number: {number}")
        return number
    if point.kind == Kind.BOOL:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw != 0
        if isinstance(raw, str):
            word = raw.strip().lower()
            if word in {v.lower() for v in address.true_values}:
                return True
            if word in {v.lower() for v in address.false_values}:
                return False
        raise PayloadError(f"not an on/off value: {str(raw)[:40]!r}")
    # Str with an enum list: the payload must be one of the names (any case).
    text = str(raw).strip()
    for name in point.enum or ():
        if name.lower() == text.lower():
            return name
    raise PayloadError(f"'{text[:40]}' is not in the enum list")


def point_value(point: RuntimePoint, payload: bytes) -> float | bool | str:
    return convert(point, extract(point.address, payload))
