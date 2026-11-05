"""MQTT brokers are buses too (milestone 4d).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = "bus_type IN ('modbus_rtu', 'modbus_rtu_over_tcp', 'modbus_tcp_gateway', 'bacnet_mstp')"
NEW = "bus_type IN ('modbus_rtu', 'modbus_rtu_over_tcp', 'modbus_tcp_gateway', 'bacnet_mstp', 'mqtt_broker')"


def upgrade() -> None:
    op.drop_constraint("bus_type", "bus", type_="check")
    op.create_check_constraint("bus_type", "bus", NEW)


def downgrade() -> None:
    op.execute("DELETE FROM device WHERE bus_id IN (SELECT id FROM bus WHERE bus_type = 'mqtt_broker')")
    op.execute("DELETE FROM bus WHERE bus_type = 'mqtt_broker'")
    op.drop_constraint("bus_type", "bus", type_="check")
    op.create_check_constraint("bus_type", "bus", OLD)
