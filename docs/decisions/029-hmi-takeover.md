# 029: HMI standby takeover (milestone 11)

Date: 7 Nov 2026. Status: decided (1, 2); proposed (3, 4), built as proposed (to confirm at review).

1. One part.
2. A shared standby token (`BMS_STANDBY_TOKEN` on both) authenticates the Pi and the server to each other on the standby endpoints only.
3. The kiosk moves between the server's HMI and the Pi's own HMI by itself: the server's HMI asks the Pi (`?standby=http://localhost`) while the server is unreachable.
4. Bus-clear checks listen without sending: Modbus RTU (no bytes for 60 s) and MS/TP (no frames from MAC 0 for 60 s); IP buses and gateways count as clear once the heartbeat is lost.

Consequences: the Pi runs the backend (PostgreSQL in RAM, as in standalone) in standby; the server must know its HMIs (`BMS_HMI_URLS`) to stay silent while one holds the buses.
