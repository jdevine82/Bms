# Commissioning a site

For installers and site admins. Everything here is in **Setup** (admins only). Each change is checked before it is
saved, goes live without a restart, and is recorded in the audit log.

## The order of work

1. **Buses and networks:** RS485 lines, serial gateways, MS/TP trunks and MQTT brokers.
2. **Rooms** (Haystack spaces), with their names.
3. **Devices and points**, with Discover and the commissioning wizard.
4. **Equipment**, mostly from equipment templates.
5. **Alarms, write limits and occupancy values** on the points.
6. **Schedules.**
7. **Graphics.**
8. **Control logic.**
9. **Checks**, then export the setup as a record.

## Names and tags

- **Point names** follow `<room>_<equipment>_<function>`, for example `plant_ahu1_supply_air_temp`. Equipment with no
  room uses `site`. Names are unique on the site.
- **Renaming** keeps the point's history, and the old name stays as an alias, so old CSV files and logic still find it.
  Use **Rename** on the item, not an edit of the name.
- **Display names** are free text and can change at any time.
- **Tags** come from the Haystack 4 library and the house library (main spec Appendix A). They drive the starting
  graphics, symbols and filters. Remember that Haystack calls supply air `discharge`. A tag in neither library is a
  site **custom** tag: allowed, and marked as custom.
- **Point types:** `sensor` (measured, never written), `sp` (setpoint) or `cmd` (command). Each has a kind: `Number`
  with a unit, `Bool` with labels, or `Str` with an enum.

## Buses and networks

| Type | Use |
|---|---|
| Modbus RTU | A USB RS485 adapter, by its stable name (`/dev/ttyBMS-…`). One adapter per line, never shared. |
| Modbus RTU over TCP | A serial-to-Ethernet gateway that passes raw RTU frames. |
| Modbus TCP gateway | A gateway that converts Modbus TCP to RTU (devices by unit ID). |
| BACnet MS/TP | A trunk run by `bms-mstp` on the host ([Site server](install-site-server.md), step 4). MAC 0 is the server and MAC 1 the Pi on every trunk; never give them to devices. |
| MQTT broker | Usually the site's own Mosquitto. The password is given only as the name of an environment variable. |

BACnet/IP and Modbus TCP devices need no bus: they have their own address. The bus screen shows each connection live,
with MS/TP trunk counters.

## Discover

**Setup → Discover** scans one network at a time:
- **BACnet/IP:** Who-Is.
- **MS/TP:** Who-Is through the trunk's service.
- **Modbus TCP:** a range of IP addresses.
- **Modbus RTU:** a range of unit IDs on one bus.

Scans never write. A scan on a live serial line asks for confirmation first, because it shares the line with polling.

Each result shows **New**, **Already added**, **Address clash** or **Missing**, with what the device reported and a
suggested template. From a result you can add it, test read it, or send it to the wizard.

## The commissioning wizard

**Setup → Commission a device**, four steps:

1. **Device:** pick a device template and the connection (or a Discover result). **Test connection** reads the whole point
   map and shows pass or fail for each point, with the raw request and answer. Nothing is saved.
2. **Equipment:** an equipment or room template (or none), the names, the room, and a repeat count for several units
   (`{n}` in the names). **Build the table** makes a draft and shows one editable table of the new points. **Test the
   new points** reads them.
3. **Verify and go live:** a checklist (logging, poll speed, communicating, write limits, alarms).
   - Errors block **Go live**. The common one: a writable Number point without write limits.
   - Warnings can be accepted.
   - Go live saves everything as one checked change.
4. **Place on graphics:** open the graphics editor (below).

Drafts are kept in your browser until Go live.

## The mapping table

**Setup → Mapping table** lists every point and lets you edit it in place:
- display name, equipment, room, tags, unit and scaling;
- write limits and occupancy values.

Filters find points with no room, untagged points, duplicate names, points that are not communicating, and writable
points with no write limits. Select rows to set a room, move them to equipment or add tags. **Export CSV**, edit it in a
spreadsheet, and **import** it again (matched by name). The whole table saves as one change; if someone else changed the
setup meanwhile, the save is refused rather than overwriting their work.

## Device and equipment templates

- **Device templates** are point maps for a device model (Modbus, BACnet or MQTT). The built-in ones are read-only; a
  site saves its own, with versions. **Apply** to a device with a name prefix, a subset of points and a repeat.
- **Equipment templates** (indoor unit, AHU, room, pump, fan, exhaust fan, ERV, light circuit, power meter, and the
  site's own) define **roles**: the points that piece of equipment has, with tags, units, alarms, write limits and
  occupancy values as defaults.
  - Editing makes a new version, with a **preview per instance** before saving.
  - **Roll out** a version to all, a group or one instance. **A point's own value always wins over the template.**
  - An instance shows each role as From template or Overridden, with **Reset <field> to template** for each overridden field and **Unlink**.
  - Built-in templates must be copied before they can be changed.

## Alarms

**Setup → Alarm definitions** (and in templates):
- **Point alarms:**
  - high or low limit (with a deadband);
  - state (a Bool or Str value);
  - stale.

  Each has a level (Info, Warning or Critical), a delay and a short message.
- **Device offline alarms:** only where you set one.
- Limit and state alarms react only to Good values. A device that stops answering raises its offline alarm instead of a
  flood of limit alarms.
- Who is told: [Alarms](alarms.md) and Setup → Alarm delivery.

## Write limits and commands

- Every writable Number point (setpoint or command) needs **write limits** (min and max). Without them it cannot be
  commanded, and the wizard and the mapping table block go-live.
- Limits apply to every role, admin included, and to logic and schedules.
- Commands go out on the bus's own connection between polls and are read back. BACnet commands from people use
  priority 8, logic uses 10 and schedules 16.

## Schedules

On the **Schedules** page (operators and admins):
- A schedule says **Occupied** or **Unoccupied**, with a weekly pattern, holiday calendars and one-off exceptions. A one-off
  beats a holiday, which beats the week.
- It applies to equipment, or to equipment with a tag. Equipment belongs to one schedule at most.
- Points follow it through their **occupancy values** (Mapping table: for example On and Off, or 22 and 26). Without one,
  a point does not follow the schedule.
- **Controller copy:** a schedule can be written into a BACnet controller's Schedule object, so the equipment keeps its
  times if the server is down. **Check the controller** compares its copy; **Keep the controller's weekly times**
  takes a local change. BACnet/IP controllers only for now.

## Graphics

1. Set up rooms and equipment first. The **starting graphics** are made from them: a Site graphic with each room, and a
   graphic per room with its equipment's symbols chosen by tags.
2. **Setup → Graphics → Open in editor**, or **New graphic**. Desktop browsers only.
   - **Palette (left):** symbols and tools for pipe, duct, room, live value and text. Click one, then click the drawing.
     For pipes and ducts, click each corner and press Enter.
   - **Moving:**
     - drag to move, and drag the square to resize;
     - arrow keys nudge, Delete removes, Ctrl+D duplicates, Ctrl+Z and Ctrl+Y undo and redo;
     - everything snaps to a 10-unit grid.
   - **Properties (right):** bind points by typing part of the name; set colour rules (follows a symbol, up to four
     value bands with hysteresis, or proportional fill), labels, size and rotation. With nothing selected: the graphic's
     name, kind, room, size, tab and background (an SVG floor plan up to 1 MB).
   - Values show live while you edit, so a wrong binding shows as `--` straight away.
   - **Check**, then **Save**.
3. **Tab order:** Setup → Graphics → Tab order. The Site graphic comes first.
4. Once graphics are saved they stop following setup changes. Add new equipment by hand, or use **Make starting graphics
   again**, which replaces every saved graphic.
5. **Site symbols:**
   - **Draw a symbol** from simple shapes in the Off layer, the On layer or both, with an animation for On.
   - Or **Import an SVG**: in Inkscape, put the Off drawing in a layer named Off and the On drawing in a layer named On,
     and set lines and fills that should take the state colour to `currentColor`.
   - Imports are cleaned of scripts, links and anything else that is not drawing; the screen lists what was removed.
   - Limits: 64 KB for a symbol and 1 MB for a background.

The library symbols include Equinor's engineering-symbols (MIT licence); the credit ships with them.

## Control logic

On the **Logic** page (admins edit; everyone can watch in Live mode):

1. **New diagram**, add blocks from the palette, and set each on the right:
   - its point, variable or schedule;
   - values for unwired inputs;
   - each output's **fail-safe**: release, hold, or a value.
2. Wire an output (right side) to an input (left side). Ports only join the same type (true/false, number or text).
3. The draft saves itself. **Checks** on the right list what would stop publishing; click one to select its block.
4. **Test** below the canvas: set each input point to a value or Offline, and **Run**. Nothing is written.
5. **Publish**, confirming each output's fail-safe. **Live** shows it running, with values on every wire.

Rules to know:
- **One writer per point.** A point written by logic cannot also follow a schedule or be written by another diagram.
- Hand mode always beats logic. When hand mode ends, the point gets logic's value.
- A Bad input makes the output go to its fail-safe. So does disabling, unpublishing or removing the diagram, or stopping
  the server.
- Logic writes at BACnet priority 10, within the point's write limits, only when the value changes (and every 10 minutes).

Further features:
- **Template logic:** Equipment templates → a site template → **Logic** opens the editor with roles instead of points.
  Publishing makes a new template version; roll it out to instances. Each instance runs its own copy, and has its own
  switch.
- **Variables as points:** **Publish as point** on a variable makes it a normal point, for graphics, trends, alarms and
  the touch screen's Setpoints (with a PIN). A constant becomes a setpoint.
- **Snippets:** select blocks, **Save as snippet**, and insert it from the palette on any diagram.
- **Import and export:** `<name>.bmslogic.json` files or pasted text. Points that are not found are mapped in a table.
- **Setup → Equipment** shows whether each piece of equipment is BMS-controlled (logic writes it) or device-controlled.

## The Haystack API (if the customer wants it)

1. **Setup → Users → Add**, tick **Haystack API client**, and give the program a user name and password.
2. **Setup → Security**, tick **Haystack API on**. The address is shown (`https://<site>/api/haystack` over Tailscale).
3. Give the address, user name and password to the customer's tool.

It is read-only (model, live values, history, watches, in Haystack JSON or Zinc) and uses Haystack SCRAM login. Only API
client accounts can use it; they cannot log in to the web app. Limits:
- 31 days of history a request;
- 10 watches;
- 30 requests every 10 seconds;
- tokens end after 60 minutes unused.

**Setup → Site → Export Haystack JSON or Zinc** saves the model as a file.

## Finishing

- Every point Good, with no "No write limits" or "not communicating" rows in the mapping table.
- Alarm delivery tested (Test buttons), and somebody receives Critical.
- Schedules show the right state now and the next change.
- Graphics checked on a phone and on the touch screen.
- **Export the setup** (Setup → Site) and keep it with the site's records.
