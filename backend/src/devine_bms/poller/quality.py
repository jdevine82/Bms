"""Value quality and history rows (main spec 5.2.1, milestone 2 spec 4 and 6).

The tracker turns read results into live values and history rows:
- a Good read updates the value and, for logged points, writes a history row;
- a change to Bad (down or fault) or Stale writes one history row with the
  reason, so gaps are recorded; repeated failures with the same reason do not;
- a point with no Good value for its stale time becomes Stale.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from devine_bms.config.model import POLL_INTERVAL_S, Kind, PollClass
from devine_bms.drivers.modbus.codec import DecodeError, decode_bit, decode_registers
from devine_bms.poller.blocks import BIT_OBJECTS, Block, span
from devine_bms.runtime_model import RuntimePoint
from devine_bms.values import HisRow, LiveValue, Status, ValueCache


def now_utc() -> datetime:
    return datetime.now(UTC)


def point_value(point: RuntimePoint, block: Block, raw: list) -> float | bool | str:
    """Decode one point's value from a block read. Raises DecodeError for a bad value."""
    offset = block.offset_of(point)
    if point.address.object_type in BIT_OBJECTS:
        return decode_bit(raw[offset])
    registers = raw[offset : offset + span(point)]
    a = point.address
    number = decode_registers(registers, a.data_type, a.byte_order, a.word_order, a.scale, a.offset)
    if point.kind == Kind.STR:
        index = int(number)
        if not point.enum or not 0 <= index < len(point.enum) or index != number:
            raise DecodeError(f"value {number} has no entry in the enum list")
        return point.enum[index]
    return number


class QualityTracker:
    def __init__(
        self,
        points: list[RuntimePoint],
        cache: ValueCache,
        emit_history: Callable[[HisRow], None],
        intervals: dict[PollClass, float] = POLL_INTERVAL_S,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.points = {p.id: p for p in points}
        self.cache = cache
        self.emit = emit_history
        self.intervals = intervals
        self.clock = clock
        self.started = clock()
        # Stale times that differ from the point's class, for example points kept up to date by
        # BACnet change-of-value, which only report on a change (main spec 5.1.4).
        self.stale_overrides: dict[uuid.UUID, float] = {}
        for p in points:
            cache.set(LiveValue(p.id, None, Status.UNKNOWN, self.started, "not read yet"))

    def good(self, point: RuntimePoint, value: float | bool | str) -> None:
        ts = self.clock()
        self.cache.set(LiveValue(point.id, value, Status.OK, ts, None, ts))
        if point.his:
            num = None if isinstance(value, str) else float(value)
            text = value if isinstance(value, str) else None
            self.emit(HisRow(point.id, ts, num, text, Status.OK, None))

    def bad(self, point: RuntimePoint, status: Status, reason: str) -> None:
        current = self.cache.get(point.id)
        if current and current.status == status and current.reason == reason:
            return  # no change; nothing new to record
        ts = self.clock()
        last_value = current.value if current else None
        last_good = current.last_good_ts if current else None
        self.cache.set(LiveValue(point.id, last_value, status, ts, reason, last_good))
        if point.his:
            self.emit(HisRow(point.id, ts, None, None, status, reason))

    def check_stale(self) -> None:
        now = self.clock()
        for point in self.points.values():
            current = self.cache.get(point.id)
            if current is None or current.status not in (Status.OK, Status.UNKNOWN):
                continue
            since = current.last_good_ts or self.started
            limit = self.stale_overrides.get(point.id) or point.stale_after(self.intervals)
            if (now - since).total_seconds() > limit:
                self.bad(point, Status.STALE, f"no new value for more than {limit:g} s")

    def point_id_set(self) -> set[uuid.UUID]:
        return set(self.points)
