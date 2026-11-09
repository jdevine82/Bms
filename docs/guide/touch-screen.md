# The touch screen

For operators. The touch screen (HMI) on the plant room wall shows the site at a glance and lets you change things after
entering your PIN.

## The top bar

- The site's name and the time.
- **Connected**, or a warning when the screen cannot reach the BMS server.
- **View only**, or the name of the person who has unlocked it.
- On a standalone site, **Standalone**, the USB export status and the number of alarms.

## The screens (bottom bar)

| Tab | What it shows |
|---|---|
| **Overview** | Active alarms, and a tile for each piece of equipment with its values. Values that are not Good show `--` and why. |
| **Graphics** | The site and its rooms as drawings (see [Using the web app](using-the-web-app.md), "Graphics"). Tap a room to open it, or a symbol to control it. |
| **Setpoints** | Every setpoint and command, grouped by equipment, with large controls. |
| **Schedules** | Each schedule's state now, why, and until when. |
| **Trends** | A short trend of one point: 1, 6 or 24 hours. Longer history is in the web app. |

## Changing something: the PIN

1. Tap **Unlock with PIN** (or try to change something), tap your name, then enter your PIN and press **Unlock**.
2. Make the change. Setpoints only accept values inside their limits. Starting and stopping equipment, and every hand
   mode, ask you to confirm.
3. The screen locks again after 5 minutes without use, or press **Lock now**.

You set your PIN in the web app, on **My account → HMI PIN**. Five wrong PINs lock your account for 10 minutes.

## When the server cannot be reached

A banner says **The site server cannot be reached**, and values are greyed because they are no longer live. The screen
reconnects by itself when the server is back.

### If your touch screen can take over (standby)

Some sites have a touch screen that **takes over** when the server stops:

1. After about 30 seconds without the server, and once the equipment wiring is quiet, the screen switches to **Direct
   mode**. A red banner says the site server is down and this screen is driving the plant. A **Critical** alarm is
   raised.
2. In direct mode you can view everything and use setpoints and hand mode, with your PIN. **Schedules and control logic
   do not run**: equipment relies on its own controllers.
3. When the server is back, the banner says so. Open the **Server** tab. After the server has been healthy for 5
   minutes, **Hand control back** is offered: tap it and enter your PIN.
4. Handing back returns every hand mode set on this screen to automatic, and sends this screen's record of changes to
   the server's audit log. The screen then goes back to normal.

The screen never hands back by itself. Someone must check the server is really back first.

## Standalone sites

On a standalone site, the touch screen is the whole BMS. Its bottom bar is different:

| Tab | What it shows |
|---|---|
| **Graphics** | As above. |
| **Overrides** | Setpoints with their allowed range (PIN to change), and every hand mode with its time left and **Back to Auto**. |
| **Schedules** | As above. |
| **Log** | Temperatures and power for the last 1, 6, 24 or 72 hours, the USB export status, **Export now** and **Download CSV**. |

Things to know:
- The log is kept in memory for 72 hours, one reading a minute. Leave a USB stick in to keep it: a file for each month
  grows every hour. The top bar warns if the stick is missing.
- After a power cut, the setup, accounts and schedules come back, but **hand modes return to automatic** and the log and
  alarm history are cleared.
- Setting up is done from a laptop or phone, not on the touch screen (see your installer).
