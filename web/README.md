# Devine BMS web app

React + TypeScript + Vite. Milestone 2 spec: `../docs/specs/m2-simulated-modbus-to-trend.md` section 8.

- **Points page:** every point with its live value, status and last update, updated over the WebSocket.
  Bad and Stale values show `--` with the reason; they are never shown as a live number.
- **Trends page:** up to 4 points, last hour, 24 hours, 7 days, 30 days or a custom range, drawn with uPlot.
  Times are in the site time zone, gaps show where a value was Bad or Stale, and each point has an
  **Export CSV** button for the plotted range.
- Light and dark themes (follows the system, or pick one). Works on a phone; trends are laid out for desktop first.

## Develop

Start the simulator and the backend API first (see `../backend/README.md`), then:

```bash
npm install
npm run dev
```

Open http://127.0.0.1:5173. The Vite server passes `/api` (including the live WebSocket) to the backend on port 8000.

## Build

```bash
npm run build
```

This type-checks and writes `dist/`. The backend serves it at `/` when `BMS_WEB_DIST` points at it.

## Browser test

With the backend serving the built app on port 8000:

```bash
BMS_URL=http://127.0.0.1:8000 npm run test:e2e
```

It checks that live values appear, the trend chart draws, the CSV downloads, and values are visible at phone width.

The HMI layout (`/hmi`, milestone 3) has its own test at 1024 x 600 and 1280 x 720, including the server-not-reachable
banner and the Pi waiting page:

```bash
BMS_URL=http://127.0.0.1:8000 npm run test:hmi
```
