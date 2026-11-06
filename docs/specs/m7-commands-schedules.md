# Milestone 7: commands, override timers and schedules

Status: **7A and 7B built** (6 Nov 2026). Main spec: 5.4 (schedules), 5.6 (commands and safety), 6.2 (HMI PIN), 7 (roles), 18 (milestone 7).

Schedules and override timers both need **commands** (writing to devices), which did not exist before this milestone, so commands come first.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How it is split | **Two parts.** **7A:** commands on every protocol, write limits, confirm prompts, hand mode with override timers, HMI PIN, audit. **7B:** schedules: the BMS calendar (recurrence, holidays, exceptions, priority layers, applied to equipment or tags) and writing schedules into BACnet controllers with a check for local changes. |
| 2 | BACnet write priorities | **8 (Manual Operator) for operator commands and hand mode**, released (written null) when an override ends; **16 for BMS schedules**. Controller logic and life-safety at higher priorities keep winning. |
| 3 | When hand mode ends on a device with no priorities (Modbus, MQTT) | **The schedule's current value if a BMS schedule drives the point, otherwise the value from before the override.** |

## 7A: commands and override timers

### What can be commanded

- Points of type **sp** (setpoint) and **cmd** (command); sensors are never written (main spec 5.2).
- **Modbus:** coils (function 05) and holding registers (06, or 16 for 32-bit types), with the point's scale and offset reversed. Discrete inputs and input registers are read-only.
- **BACnet/IP and MS/TP:** WriteProperty of presentValue at the priority above, on analog, binary and multi-state value and output objects. Inputs are read-only. The MS/TP service gets a `WRITE` command.
- **MQTT:** a `commandTopic` on the point's address, with an optional `commandTemplate` (for example `{"state": "{value}"}` for Zigbee2MQTT) and the texts for true and false (default `ON` and `OFF`).

### Safety (main spec 5.6)

- **Write limits** (`writeLimits: {min, max}`) on Number setpoints and commands, enforced by the server for **every role, admin included**. A Number point with no limits cannot be commanded until it has them; the setup checks warn, and commissioning lists it.
- **Confirm prompts** in the web app and HMI for start/stop (Bool commands) and for every hand-mode override.
- Commands go through the bus's own connection, ahead of the next poll, so a serial line is never opened twice; the point is read back straight after.
- Every command is in the audit log: user, time, point, old and new value, and the override time.

### Two kinds of command

- **Set** (setpoints): a lasting change, such as a new set temperature. On BACnet it is written at priority 8 and stays.
- **Hand mode** (overrides): the operator picks **1, 2 or 8 hours, default 2** (a site setting can change the choices and the default). When the time ends the point **returns to auto**: on BACnet the priority-8 value is released; on Modbus and MQTT the BMS writes the schedule's value, or the value from before. **Release to auto** ends it early. Overrides are stored, so they survive a restart, and they show on the screens with who set them and when they end.

### Roles and the HMI PIN (main spec 7 and 6.2)

- Operators and admins command; viewers and kiosks cannot.
- On the HMI, commands need a **PIN**: the person picks their name and enters their PIN (at least 4 digits, set on My account, separate from the password). It unlocks commands for 5 minutes idle, then locks. Lockout and rate limits as for passwords.

### Screens

- Web app: a command control on each writable point (set a value or hand mode with the time, release to auto), the override badge and its end time, and a list of active overrides.
- HMI: the **Setpoints** tab becomes live: setpoints and commands with large controls, behind the PIN.
- Setup: write limits in the point editor, mapping table and commissioning checks; the override times in the site settings.

### Built in 7A

- **Config:** `writeLimits: {min, max}` on points and on equipment template roles (an override like any other role field); `commandTopic`, `commandTemplate`, `commandTrue` and `commandFalse` on MQTT addresses. Checks: limits only on Number setpoints and commands; a writable Number point without limits is a warning in the setup and an **error that blocks go-live** in the commissioning checks and the mapping table (as the mockup shows). The built-in templates now carry limits (indoor unit 17 to 30 °C, mode 1 to 5, fan speed 1 to 4; AHU supply air 12 to 30 °C; valves, dampers and speeds 0 to 100 %). The example configs' setpoints have limits.
- **Writer** (`commands/writer.py`): Modbus FC05, FC06 and FC16 with the point's scale, offset, byte and word order reversed (`codec.encode_registers`); BACnet/IP WriteProperty with priority and release (Null); MS/TP through the service's new **`WRITE`** command (`bms-mstp` 0.2.0, tested against bacnet-stack's own MS/TP server, including releasing a priority); MQTT publish (QoS 1, not retained).
- **Command service** (`commands/service.py`): value and limit checks, `confirm` for Bool commands and every override, **set** and **hand** (the site's choices, default 1, 2 or 8 hours, 2 by default), release to auto, override expiry every 5 seconds, overrides stored in `point_override` (migration `0011`). Before hand mode starts, the point is **read from the device** for the value to return to, and every command's read-back becomes the live value at once (found while testing: the last poll can be seconds old).
- **API:** `POST /api/points/{id}/command`, `POST /api/points/{id}/release`, `GET /api/overrides`, `GET`/`PUT /api/command-settings`; `/api/points` gives `writable`, `writeLimits` and `override`; override changes are pushed on the live WebSocket. Every command, release, timer end and failure is in the audit log with the old and new value.
- **HMI PIN:** set on My account (4 to 12 digits, not all one digit or a run), removed by an admin. `GET /api/auth/pin-users` and `POST /api/auth/pin` only answer a listed kiosk (or the server itself). A PIN session ends after 5 minutes idle; wrong PINs count towards the lockout.
- **Screens:** a Command button on writable points (set, hand mode with its time, release to auto, the limits shown, confirm step); a hand-mode badge with who and until when; the HMI Setpoints tab (grouped by equipment, PIN pad, Lock now, large controls); write-limit columns and a "No write limits" filter in the mapping table and commissioning table; override times in Setup → Site.
- **Simulators:** the Modbus meter has writable registers 20 to 23 and the I/O module takes coil writes; the BACnet simulator's setpoint is commandable; the MS/TP stand-in keeps a priority array.
- **Tests:** 222 backend tests (new: the encoder, writes on all four protocols, BACnet priorities and release, the real MS/TP `WRITE`, MQTT templates, limits, confirm, set and hand, expiry back to the previous value, release, set ending hand mode, a restart, override choices, roles, kiosk and PIN). Browser: `npm run test:commands` (14 checks).

### Notes

- Bool commands on the screens are always hand mode (with a time), as start/stop overrides; a lasting change is for setpoints. The API still accepts `set` for a Bool command.
- On BACnet, **set** writes at priority 8 and stays until released; the Command panel offers Release to auto on BACnet points.
- A write to an MQTT device is not read back; the device reports its new state on its own topic.

## 7B: schedules

### Decisions (6 Nov 2026)

| # | Question | Decision |
|---|---|---|
| 4 | What a schedule controls | **Occupancy.** A schedule says Occupied or Unoccupied over time and applies to equipment, or to a group of equipment by tag. Each writable point that should follow it has an **occupancy map** (`occupancy: {occupied, unoccupied}`), for example an on/off command On and Off, a setpoint 22 and 26. Equipment template roles carry these maps as defaults. |
| 5 | Exceptions | **Holiday calendars and one-off exceptions.** Shared holiday calendars (public holidays, closures) make a day Unoccupied; one-off exceptions (a date and time range, Occupied or Unoccupied) belong to one schedule. **A one-off beats a holiday, which beats the week.** |
| 6 | Schedules in controllers | **Built now, checked on the bench later.** A schedule can be written into a BACnet Schedule object on a controller (weekly pattern and exceptions), so the equipment keeps its times if the server fails. The BMS reads it back and flags a controller whose schedule was changed locally; the operator re-pushes or accepts the controller's weekly pattern. |
| 7 | Who changes schedules | **Operators and admins edit everything** about schedules and holiday calendars (main spec 7 is updated). The occupancy maps on points are setup, so they stay with admins. |

### How it works

- Schedules and holiday calendars live in their own tables (not the config file), because operators change them day to day; every change is in the audit log. Times are in the site's time zone.
- An equipment belongs to **one schedule at most**; the screens refuse a second.
- **Running:** every 15 seconds the BMS works out each schedule's state; when it changes (and at start-up) it writes each mapped point of its equipment: on BACnet at **priority 16**, so hand mode at priority 8 still wins; on Modbus and MQTT a point in hand mode is skipped and gets the schedule's value when hand mode ends (decision 3).
- Screens: a **Schedules** page (state now and next change, the weekly pattern as day rows, holiday calendars, one-off exceptions, what it applies to, the controller copy and its sync state); the HMI **Schedules** tab shows them; occupancy maps in the mapping table and the equipment template editor.
- **Limit:** controller schedules are written over BACnet/IP. MS/TP controllers need the service to send structured values, which comes after the bench check.

## Testing

- 7A: writes through the Modbus simulators (coils and registers), the BACnet simulator (priority array, relinquish), the MS/TP stand-in and the MQTT simulator; limits for every role; override expiry and restart; the HMI PIN.
- Bench: a real write to each device type in section 20, checked on the device.

### Built in 7B

- **Config:** `occupancy: {occupied, unoccupied}` on points and equipment template roles (an override like any other role field), checked against the point's kind, enum and write limits. The built-in templates' run and on/off commands follow occupancy (On when occupied, Off when not).
- **Tables** (migration `0012`): `schedule` and `holiday_calendar`, outside the config file. API: `/api/schedules` (list, add, change, remove, each with its state now, next change, the equipment it covers and the points that follow it), `/api/schedules/{name}/controller/push|check|accept`, `/api/holiday-calendars`. A schedule sent back as it was read is accepted (its worked-out fields are ignored).
- **Runner** (`schedules/service.py`): every 15 seconds; writes on a change of a point's wanted value and at start-up; BACnet at priority 16; Modbus and MQTT points in hand mode are skipped, and **hand mode now ends at the schedule's value** (the CommandService hook); failed writes are tried again on the next pass; each state change is one audit entry (written, in hand mode, failed).
- **Controller copy** (`schedules/bacnet.py`): `scheduleDefault`, `weeklySchedule` (Monday first) and one `exceptionSchedule` entry per date a holiday or one-off touches within a year (BACnet exceptions replace a whole day, so the BMS writes the day's full timeline), priority 1. Check compares the controller's copy with what the BMS would write and lists the differences in words; Accept takes the controller's weekly times into the BMS schedule (not its exceptions). The BACnet simulator has a Schedule object that stores what it is written.
- **Screens:** **Schedules** in the header (everyone views; operators and admins edit): the list with each state, the weekly pattern as day rows (Same as Monday), holiday calendars, one-off exceptions, what it applies to (equipment and tags) and the points that follow, and the controller copy with Write, Check and Keep the controller's times; a Holiday calendars tab; the HMI **Schedules** tab (state, why, until when); occupancy values in the mapping table.
- **Tests:** 226 backend tests (new: the rule and its precedence, whole days and validation, the controller plan, writing on Occupied and Unoccupied, one schedule per equipment, hand mode and its end at the schedule's value, holidays, roles, push, check, a local change and accept). Browser: `npm run test:schedules` (11 checks). Found while testing: the header no longer fitted a phone screen with the new buttons; it now wraps.

### Not built yet

- Controller copies on MS/TP controllers (the service sends single values only), and reading a controller's own Calendar objects.
- Occupancy editing in the equipment template editor (templates carry it in their files; the mapping table edits it per point).
