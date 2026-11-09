# 033: Maintenance counters (main spec 5.10)

Date: 9 Nov 2026. Status: decided.

1. After the guides, the remaining v1 work is done in this order: maintenance counters, then the support tools (diagnostics, support bundle, live traffic viewer).
2. **Counter points are made automatically** for every piece of equipment with a run-state point (a Bool sensor tagged `run`): `<room>_<equip>_run_hours` and `<room>_<equip>_starts` on a built-in `bms_counters` device. They are real, read-only points, so trends, alarms and logic use them like any other.

Consequences: the setup gains a `counter` device protocol, `CounterAddress` points, `service` intervals on equipment and templates, and `counters` in the export; the counts themselves live in `equip_counter`.
