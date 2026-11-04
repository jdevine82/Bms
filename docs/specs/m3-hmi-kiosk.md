# Milestone 3: Pi HMI boots into the UI

Status: **decided** (4 Nov 2026). Items marked **[Assumed]** are build details that can change during the build.
Main spec: section 6.2 (Pi HMI), 6.3 (HMI mockups), 13 (Raspberry Pi HMI), 18 (milestone 3).

## 1. Goal

A Raspberry Pi with a touch screen **boots straight into the HMI** in full mode: no desktop, no login prompt, no browser controls. The HMI is the same web app as the site web app, in an **HMI layout** at `/hmi`, loaded from the site server.

## 2. Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Screen | **Not chosen yet.** The HMI layout must work at **1024 x 600** (the mockups' size) and **1280 x 720** (the official Raspberry Pi Touch Display 2, rotated to landscape). Both are tested. |
| 2 | What the HMI shows | **The HMI shell plus what exists now:** the layout from the mockups (top bar, five bottom tabs, dark theme, touch targets of at least 44 px). **Overview** shows live values by equipment, and **Trends** shows short trends. **Graphics, Setpoints and Schedules** show that they come in a later milestone. |
| 3 | Kiosk | **Raspberry Pi OS Lite (64-bit) with `cage`**, a compositor that runs one full-screen app. A systemd service starts Chromium in kiosk mode and restarts it if it stops. The Pi's hardware watchdog reboots the Pi if the system hangs. |

The Pi is a Pi 4 or Pi 5 with 64-bit Raspberry Pi OS only (main spec 13, decision 013).

## 3. Out of scope (later milestones)

- PIN login and commands from the HMI (milestones 6 and 7; main spec 6.2 and 7).
- Graphics tabs, setpoints and schedules screens (milestones 7 and 9).
- Direct mode and takeover when the server fails (milestone 11; main spec 9). In this milestone the HMI only shows that the server cannot be reached.
- Standalone mode, where the Pi runs the backend itself (milestone 11a), and the read-only root filesystem it uses.
- The first-boot setup page with the one-time setup code (main spec 3.1). The provisioning script takes the server address instead.
- Remote updates of the Pi (milestone 12).

## 4. HMI layout (web app, `/hmi`)

The same React app as the site web app. It picks the HMI layout when the path starts with `/hmi`. The backend serves the app at `/hmi` as well as `/`.

- **Dark theme only** (main spec 6.3). Status is shown by text and shape as well as colour.
- **Top bar:** site name, the server connection state, a clock in the site time zone, and a **View only** label (PIN login comes later).
- **Bottom bar:** five tabs, **Overview, Graphics, Setpoints, Schedules, Trends** (main spec 6.3), each at least 44 px high.
- **Overview:** a tile per piece of equipment (and per room for points that belong to a room directly), listing its points with live value, unit and status. Bad and Stale values show `--` and the reason (main spec 5.2.1).
- **Trends:** pick one point and a range of **1, 6 or 24 hours** (short trends, main spec 6.2; long history is in the web app). Gaps show where a value was Bad or Stale.
- **Graphics, Setpoints, Schedules:** a short note that the screen arrives in a later milestone.
- **Server not reachable:** if the live connection drops for more than 5 seconds, a banner says the server cannot be reached and values are no longer live. Values are greyed. It recovers by itself when the server returns.
- **Touch:** no hover-only controls, no text selection, no pinch zoom, and no pull-to-refresh or swipe-back navigation.
- **Screen sizes:** no scrolling of the page sideways at 1024 x 600 or 1280 x 720. Overview scrolls up and down if there are many tiles.

## 5. The Pi (`hmi/`)

**`hmi/provision.sh`**, run once as root on a fresh Raspberry Pi OS Lite (64-bit):

```bash
sudo ./provision.sh --server http://<site server address>:8000
```

It:
1. Refuses to run unless the OS is 64-bit (`aarch64`) Raspberry Pi OS or Debian (decision 013).
2. Installs `cage`, Chromium and fonts from the Raspberry Pi OS packages.
3. Creates a locked system user `bms-kiosk` with no password, in the `video`, `input` and `render` groups.
4. Writes `/etc/devine-bms/hmi.env` with the server address.
5. Installs the **waiting page** (below) to `/opt/devine-bms/hmi/`.
6. Installs and enables **`bms-kiosk.service`**: cage plus Chromium on `tty1`, started at boot, **restarted 3 seconds after it stops**, replacing the login prompt on `tty1`.
7. Turns on the **hardware watchdog** through systemd (`RuntimeWatchdogSec=15s`), so a hung Pi reboots by itself.
8. Turns off screen blanking.
9. Sets the boot target to graphical, so the kiosk starts at every boot.

Running it again is safe: it rewrites the same files. **`hmi/uninstall.sh`** removes the kiosk and returns the Pi to the login prompt.

**Chromium** runs in kiosk mode, incognito (no profile to corrupt on power loss), with no first-run screens, crash bubbles, translate prompts, pinch zoom or swipe navigation. **[Assumed]**

**Waiting page** (`hmi/waiting.html`, local on the Pi): Chromium opens it first. It shows "Connecting to the site server", the server address, and the time, and checks `/api/health` on the server every 3 seconds. When the server answers, it opens `<server>/hmi`. If Chromium restarts, it starts from here again.

**Screen rotation:** the Touch Display 2 is portrait by default. Rotation to landscape is set in the Pi's display settings, which depend on the screen chosen. The README explains both screens. **[Assumed]**

## 6. Tests

- **Browser tests (Playwright)** of `/hmi` at 1024 x 600 and 1280 x 720 with touch emulation:
  - the tabs work, and every tab and button is at least 44 px high;
  - the page never scrolls sideways;
  - Overview shows live values;
  - the server-not-reachable banner appears when the backend stops, and clears when it returns;
  - Trends draws a chart.
- **Waiting page test:** opened with the server down, it waits; when the server starts, it moves to `/hmi`.
- **`shellcheck`** on the scripts.
- **On a real Pi (bench, by hand, because this cannot be done in CI):**
  1. Fresh Raspberry Pi OS Lite 64-bit, run `provision.sh`, reboot: the HMI appears with no login prompt or desktop. Time from power on to a usable HMI is under 60 seconds (main spec 17).
  2. Kill Chromium (`sudo pkill chromium`): it is back within 10 seconds.
  3. Stop the site server: the banner appears. Start it again: values return without touching the Pi.
  4. Power off with the server down, power on: the waiting page shows, then the HMI once the server is up.
  5. Hang test for the watchdog (`echo c | sudo tee /proc/sysrq-trigger`): the Pi reboots by itself into the HMI.
  6. 32-bit Raspberry Pi OS: `provision.sh` refuses to install.

## 7. Done when

- The browser and waiting page tests pass, and shellcheck is clean.
- The six bench checks pass on a Pi 4 or Pi 5. They need hardware, so they are done on the bench and recorded in the pull request or a decision note.
