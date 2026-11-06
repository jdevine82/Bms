# Milestone 7: commands, override timers and schedules

Status: **planned** (6 Nov 2026). Main spec: 5.4 (schedules), 5.6 (commands and safety), 6.2 (HMI PIN), 7 (roles), 18 (milestone 7).

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

## 7B: schedules

Planned after 7A; the questions for it (which equipment, exception types, controller support) are asked then.

## Testing

- 7A: writes through the Modbus simulators (coils and registers), the BACnet simulator (priority array, relinquish), the MS/TP stand-in and the MQTT simulator; limits for every role; override expiry and restart; the HMI PIN.
- Bench: a real write to each device type in section 20, checked on the device.
