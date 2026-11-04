"""History responses: thinning for long ranges and CSV export (milestone 2 spec 7)."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

from devine_bms.values import HisRow, Status

MAX_ROWS = 10_000
BUCKETS = MAX_ROWS // 2  # each bucket keeps its minimum and maximum


def thin(rows: list[HisRow], start: datetime, end: datetime) -> tuple[list[HisRow], bool]:
    """Keep at most about MAX_ROWS rows: the min and max Good value in each time bucket,
    plus every row that is not Good, so gaps and their reasons are never thinned away."""
    if len(rows) <= MAX_ROWS:
        return rows, False
    width = (end - start) / BUCKETS
    kept: list[HisRow] = []
    bucket: list[HisRow] = []
    bucket_index = None

    def close_bucket() -> None:
        if not bucket:
            return
        numeric = [r for r in bucket if r.num is not None]
        if not numeric:
            kept.append(bucket[0])
            return
        low = min(numeric, key=lambda r: r.num)
        high = max(numeric, key=lambda r: r.num)
        kept.extend(sorted({id(low): low, id(high): high}.values(), key=lambda r: r.ts))

    for row in rows:
        if row.status != Status.OK:
            close_bucket()
            bucket, bucket_index = [], None
            kept.append(row)
            continue
        index = int((row.ts - start) / width)
        if index != bucket_index:
            close_bucket()
            bucket, bucket_index = [], index
        bucket.append(row)
    close_bucket()
    return kept, True


def row_json(row: HisRow) -> dict:
    value = row.str_ if row.str_ is not None else row.num
    return {"ts": row.ts.isoformat(), "value": value, "status": row.status.value, "reason": row.reason}


def history_csv(rows: list[HisRow], point_name: str, unit: str | None, zone: ZoneInfo) -> str:
    """CSV with site local time, the point name and unit in the header (main spec 5.7)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    label = f"{point_name} ({unit})" if unit else point_name
    writer.writerow([f"time ({zone.key})", label, "status", "reason"])
    for row in rows:
        local = row.ts.astimezone(zone).isoformat(sep=" ", timespec="seconds")
        value = row.str_ if row.str_ is not None else ("" if row.num is None else format(row.num, ".10g"))
        writer.writerow([local, value, row.status.value, row.reason or ""])
    return out.getvalue()
