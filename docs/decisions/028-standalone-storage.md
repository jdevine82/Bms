# 028: Standalone storage (milestone 11a)

Date: 7 Nov 2026. Status: decided.

- **PostgreSQL in RAM plus a state file**, rather than a second, file-based implementation of every service. The Pi runs PostgreSQL with its data in tmpfs; the state file on the config partition (the setup in the export format, plus the tables a site keeps through a power cut) is restored at boot and rewritten after each saved change. One codebase serves both modes, as main spec 3.1 asks, and nothing is written to the card in normal running.
- Cost: about 100 MB of RAM for PostgreSQL on the Pi; bench tests 16 and 19 confirm it.
- **Log:** 72 hours at one sample a minute, exported to USB every hour by default.
