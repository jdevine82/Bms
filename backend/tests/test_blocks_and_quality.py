import uuid
from datetime import UTC, datetime, timedelta

from devine_bms.config.model import Kind, ModbusAddress, PollClass
from devine_bms.poller.blocks import make_blocks
from devine_bms.poller.quality import QualityTracker, point_value
from devine_bms.runtime_model import RuntimePoint
from devine_bms.values import Status, ValueCache


def pt(address, data_type="uint16", object_type="holdingRegister", poll="normal", kind=Kind.NUMBER,
       enum=None, stale=None, his=True):
    return RuntimePoint(
        id=uuid.uuid4(), name=f"p{address}", device_id=uuid.uuid4(), kind=kind, unit=None, enum=enum,
        address=ModbusAddress(object_type=object_type, address=address, data_type=data_type),
        poll_class=PollClass(poll), stale_after_s=stale, his=his,
    )


def test_neighbours_share_a_block():
    blocks = make_blocks([pt(0, "float32"), pt(2, "float32"), pt(8, "uint32")], block_size=32)
    assert len(blocks) == 1
    assert (blocks[0].start, blocks[0].count) == (0, 10)


def test_block_size_splits():
    blocks = make_blocks([pt(0), pt(10), pt(40)], block_size=16)
    assert [(b.start, b.count) for b in blocks] == [(0, 11), (40, 1)]


def test_classes_and_object_types_are_separate():
    blocks = make_blocks([pt(0), pt(1, poll="fast"), pt(2, object_type="inputRegister")], block_size=32)
    assert len(blocks) == 3


def test_bits_use_a_larger_limit():
    blocks = make_blocks([pt(0, "bit", "coil", kind=Kind.BOOL), pt(300, "bit", "coil", kind=Kind.BOOL)],
                         block_size=32)
    assert len(blocks) == 1 and blocks[0].count == 301


def test_enum_value():
    p = pt(0, kind=Kind.STR, enum=("Cool", "Heat", "Fan"))
    block = make_blocks([p], 32)[0]
    assert point_value(p, block, [1]) == "Heat"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 1, tzinfo=UTC)

    def __call__(self):
        return self.now


def make_tracker(points):
    clock, rows, cache = Clock(), [], ValueCache()
    tracker = QualityTracker(points, cache, rows.append, clock=clock)
    return tracker, clock, rows, cache


def test_good_then_down_writes_one_row_per_change():
    p = pt(0)
    tracker, clock, rows, cache = make_tracker([p])
    assert cache.get(p.id).status == Status.UNKNOWN
    tracker.good(p, 12.0)
    clock.now += timedelta(seconds=1)
    tracker.bad(p, Status.DOWN, "no answer")
    tracker.bad(p, Status.DOWN, "no answer")  # repeated failure: no new row
    clock.now += timedelta(seconds=1)
    tracker.bad(p, Status.FAULT, "exception 4")
    assert [r.status for r in rows] == [Status.OK, Status.DOWN, Status.FAULT]
    assert rows[1].num is None and rows[1].reason == "no answer"
    live = cache.get(p.id)
    assert live.status == Status.FAULT and live.value == 12.0  # last value kept, shown greyed


def test_stale_after_three_intervals_by_default():
    p = pt(0, poll="normal")  # 30 s, so stale after 90 s
    tracker, clock, rows, cache = make_tracker([p])
    tracker.good(p, 1.0)
    clock.now += timedelta(seconds=89)
    tracker.check_stale()
    assert cache.get(p.id).status == Status.OK
    clock.now += timedelta(seconds=2)
    tracker.check_stale()
    assert cache.get(p.id).status == Status.STALE
    assert rows[-1].status == Status.STALE


def test_stale_time_can_be_set_per_point():
    p = pt(0, stale=5)
    tracker, clock, _, cache = make_tracker([p])
    tracker.good(p, 1.0)
    clock.now += timedelta(seconds=6)
    tracker.check_stale()
    assert cache.get(p.id).status == Status.STALE


def test_points_not_logged_write_no_history():
    p = pt(0, his=False)
    tracker, _, rows, cache = make_tracker([p])
    tracker.good(p, 1.0)
    tracker.bad(p, Status.DOWN, "x")
    assert rows == [] and cache.get(p.id).status == Status.DOWN
