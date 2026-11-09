# Maintenance counters (main spec 5.10)

Status: **built** (9 Nov 2026), while the bench is set up. Main spec 5.10 (decided), 5.5 (alarms), 6.4 (equipment panel), 3.1 (standalone storage). Decision 033.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Order of the remaining work | **Counters first, then the support tools** (diagnostics, support bundle, traffic viewer), as separate PRs. |
| 2 | How counters become points | **Made automatically.** Equipment with a run-state point gets two read-only points on a built-in **BMS counters** device: `<room>_<equip>_run_hours` (h) and `<room>_<equip>_starts`. They are created and removed with the run point, kept in the setup like other points, logged for trends, and can carry alarms. |

## How it works

- **Run-state point:** a Bool **sensor** on the equipment, tagged `run`, on a real device (the first by name if there are several). The counter points are tagged `devineRunHours` and `devineStarts` (house tags, Appendix A).
- **Making the points:** every setup save brings them up to date; so does start-up, for sites set up before counters.
  - A person's edits to a counter point (its display name, alarms, logging) are kept.
  - A name already used by another point is left alone (the change says so).
  - Renaming the equipment keeps the points and their history.
- **Counting:** every second, from the live run state.
  - While it is Good and on, the time counts as run time; an off-to-on change is a start.
  - Time while it is Bad or Stale is **not counted**. It is kept as "unseen" time, and the counters are shown as **estimated**.
  - Before the first reading after a start, nothing is counted either way.
- **Device counters:** a Number point on the equipment tagged `devineRunHours` (or `devineStarts`), from the device itself, is shown and used instead, so a BMS outage leaves no gap. The BMS keeps its own count as well.
- **Service reminders:**
  - `service: {hours, starts, level, message}` on the equipment, or on its equipment template as the default.
  - When the hours or starts **since the last service** reach the interval, a system alarm is raised: "Service due: <equip>, <n> hours", at the chosen level (default Info).
  - **Service done** (admins) starts the "since service" counts again; **totals never reset**. It clears the alarm and is audit-logged.
- **Storage:**
  - Table `equip_counter` (migration 0016), saved every minute and on stop.
  - **Standalone:** the counting columns are quiet in the state file, so the card is written daily and on stop, plus at once when a service is marked or a reminder fires.
- **Export:** `counters` in the setup file (total hours and starts, service marks).
  - An import uses them only for equipment that has no counts yet (moving a site to a new server). A running site's counts are never rolled back by an older file.
  - The setup's revision fingerprint and the standby HMI's cache leave counters out, because they change every minute.
- **Standby HMIs** do not count; the server does.

## Screens and API

- **Setup → Equipment:**
  - **Maintenance counters**: run hours, starts, since the last service (with who and when), estimated time, Service due;
  - **Service done** and **Service interval** (hours, starts, alarm level), for admins.
- **The command panel** (Points, tap-to-control on graphics and the HMI): "<hours> h run · <starts> starts", and "Service due".
- `GET /api/counters[?equip=]` (anyone signed in), `POST /api/counters/{equip}/service-done` (admins).

## Tests

- `tests/test_counters.py`:
  - the derive step (made, idempotent, edits kept, removed, a name clash, a rename);
  - on a running backend with the RTU I/O simulator:
    - an hour of run time and the starts from a real coil;
    - unseen time not counted;
    - the Warning reminder, then Service done (totals kept, alarm cleared, audit);
    - the export, with the revision unchanged by counting;
    - a restart from the database;
    - device-reported hours;
    - the run point removed;
    - operators refused Service done.
- `web/tests/counters.mjs` (9 checks).

## Not built

- The standalone USB export of counters (main spec 5.10 [Assumed]): counters are in the state file and the setup export.
