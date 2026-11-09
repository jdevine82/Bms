# Using the web app

For everyone at the site. What you can change depends on your role (see [Logins and users](users-and-logins.md)).

The bar at the top has the site's name, **Live** when values are updating, and the pages: **Graphics**, **Points**,
**Trends**, **Alarms** (with the number of alarms waiting), **Schedules**, **Logic** and, for admins, **Setup**. The
theme can be light, dark or follow your device.

## Reading values

- **Points** lists every value with its unit, equipment, status and when it last changed. Type in the filter box to find
  points by name or tag.
- A value that is not fresh shows its **status** instead of a number:
  - **Stale:** no new reading came in time;
  - **Bad:** the device did not answer, or answered with an error;
  - **Waiting:** not read yet.

  The BMS never shows an old number as if it were live.

## Graphics

**Graphics** shows the site as drawings: **Site** first, then a tab for each room or system. On a phone, Graphics and
Alarms are laid out for small screens first.

What the symbols mean:
- **On:** coloured, and fans and pumps turn; pipes and ducts show flow.
- **Off:** grey.
- **Hand mode:** a hand badge.
- **Fault:** flashes, with a "!" badge.
- **Offline:** a dashed outline (the device is not answering).
- Rooms are coloured by their temperature (or other value).

Tap a room to open its tab. Tap a symbol that can be controlled to open its controls (operators and admins).

## Trends

**Trends** draws the history of up to four points. Pick points on the left and a time range. Gaps in a line are times
when the value was Stale or Bad. **Export CSV** (one button for each point) saves the values in the range.

## Changing a setpoint or switching equipment

Operators and admins see a **Command** button on points that can be changed (on Points, and when tapping a symbol on
Graphics).

- **Set:** a lasting change, for example a new set temperature. You can only choose a value inside the point's limits,
  which are shown.
- **Hand mode for…:** a temporary override, for example forcing a fan on for 2 hours.
  - Choose how long (usually 1, 2 or 8 hours).
  - When the time ends, the point **returns to automatic** control by itself.
  - **Release to auto** ends it early.
  - Starting and stopping equipment always uses hand mode and asks you to confirm.

While a point is in hand mode it shows a **hand badge**, who set it and when it ends. Hand mode always wins over
schedules and control logic. Every command is in the audit log.

## Schedules

**Schedules** shows each schedule's state now (**Occupied** or **Unoccupied**) and its next change. Operators and admins
can edit:

- **The week:** the occupied times for each day (**Same as Monday** saves typing).
- **Holiday calendars:** public holidays and closures make the whole day Unoccupied. One calendar can be shared by
  several schedules.
- **One-off exceptions:** a date and time range that is Occupied or Unoccupied, for example a late event or an early
  close.
  **An exception beats a holiday, which beats the week.**
- **Applies to:** the equipment that follows the schedule, or every piece of equipment with a tag.

What "occupied" means for each point (for example On or Off, or 22 °C or 26 °C) is set by your installer.

Some schedules also have a **copy in a controller**, so the equipment keeps its times if the BMS server is ever down.
**Check the controller** shows whether someone changed the controller's own times; you can write the BMS times to it
again, or **Keep the controller's weekly times**.

## Control logic

**Logic** lists the control diagrams: what each one controls, and whether it is running. Open one and choose **Live**
to watch it work. Each wire shows its value: blue when true, grey when false. The blocks show timers counting and what
each output wrote. Only admins can change logic.

If a value that a diagram reads goes Bad, the diagram's outputs go to their safe state (set by your installer). Hand mode
always wins over logic.

## Alarms

See [Alarms](alarms.md).
