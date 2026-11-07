# 027: Logic runtime and write priority (milestone 10)

Date: 6 Nov 2026. Status: decided (1, 2); proposed (3, 4).

1. **Split into 10A (format, blocks, runtime, API), 10B (editor) and 10C (templates, HMI variables, snippets).**
2. **Logic writes at BACnet priority 10**, between operator hand mode (8) and schedules (16), so hand mode always wins and release to device relinquishes 10 only.
3. **One writer per point:** a point written by a published diagram cannot also follow a schedule or be written by another diagram (publish check). Two writers fighting over a point is the commonest commissioning fault.
4. **Modbus and MQTT:** no priorities, so logic writes on change (and every 10 minutes), skips points in hand mode, and gives the point its value when hand mode ends; release means stop writing.

Consequence: commands that end hand mode ask logic first, then the schedule, then the value before hand mode.
