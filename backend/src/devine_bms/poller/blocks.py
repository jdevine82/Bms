"""Group points into block reads (main spec 5.1.4).

Points on one device, with the same object type and speed class, are read
together when their addresses fit in one request of at most `block_size`
registers (or `block_size * 16` bits for coils and discrete inputs).
"""

from __future__ import annotations

from dataclasses import dataclass

from devine_bms.config.model import ObjectType, PollClass
from devine_bms.drivers.modbus.codec import registers_needed
from devine_bms.runtime_model import RuntimePoint

MAX_BITS = 2000  # Modbus limit for one coil or discrete input read
MAX_REGISTERS = 125  # Modbus limit for one register read
BIT_OBJECTS = {ObjectType.COIL, ObjectType.DISCRETE_INPUT}


@dataclass(frozen=True)
class Block:
    object_type: ObjectType
    poll_class: PollClass
    start: int
    count: int
    points: tuple[RuntimePoint, ...]

    def offset_of(self, point: RuntimePoint) -> int:
        return point.address.address - self.start


def span(point: RuntimePoint) -> int:
    if point.address.object_type in BIT_OBJECTS:
        return 1
    return registers_needed(point.address.data_type)


def make_blocks(points: list[RuntimePoint], block_size: int) -> list[Block]:
    groups: dict[tuple[ObjectType, PollClass], list[RuntimePoint]] = {}
    for point in points:
        groups.setdefault((point.address.object_type, point.poll_class), []).append(point)

    blocks: list[Block] = []
    for (object_type, poll_class), members in sorted(groups.items()):
        limit = min(block_size * 16, MAX_BITS) if object_type in BIT_OBJECTS else min(block_size, MAX_REGISTERS)
        members.sort(key=lambda p: p.address.address)
        current: list[RuntimePoint] = []
        start = end = 0
        for point in members:
            p_start = point.address.address
            p_end = p_start + span(point)
            if current and max(end, p_end) - start <= limit:
                current.append(point)
                end = max(end, p_end)
                continue
            if current:
                blocks.append(Block(object_type, poll_class, start, end - start, tuple(current)))
            current, start, end = [point], p_start, p_end
        if current:
            blocks.append(Block(object_type, poll_class, start, end - start, tuple(current)))
    return blocks
