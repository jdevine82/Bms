"""BACnet MS/TP trunks are buses too (milestone 4c).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = "bus_type IN ('modbus_rtu', 'modbus_rtu_over_tcp', 'modbus_tcp_gateway')"
NEW = "bus_type IN ('modbus_rtu', 'modbus_rtu_over_tcp', 'modbus_tcp_gateway', 'bacnet_mstp')"


def upgrade() -> None:
    op.drop_constraint("bus_type", "bus", type_="check")
    op.create_check_constraint("bus_type", "bus", NEW)


def downgrade() -> None:
    op.execute("DELETE FROM device WHERE bus_id IN (SELECT id FROM bus WHERE bus_type = 'bacnet_mstp')")
    op.execute("DELETE FROM bus WHERE bus_type = 'bacnet_mstp'")
    op.drop_constraint("bus_type", "bus", type_="check")
    op.create_check_constraint("bus_type", "bus", OLD)
