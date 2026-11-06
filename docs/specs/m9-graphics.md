# Milestone 9: graphics

Status: **planned** (6 Nov 2026). Main spec: 6.4 (symbols and graphics), 6.5 (tabs), 6.2 (HMI), 5.6 (commands), 18 (milestone 9).

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How it is split | **Two parts.** **9A:** the graphics model, the shipped symbol library, live rendering (states, colour rules, animation), tap to control, and the Site and room tabs on the web app and HMI, with starting graphics made automatically from the rooms and equipment. **9B:** the graphics editor in the web app, plus custom symbols and sanitised SVG import. |
| 2 | Where the symbols come from | **Equinor's engineering-symbols (MIT licence) where they fit**, recoloured by the BMS for states and the dark theme, and **the missing ones drawn for this project in the same line style** (light, door, meter, compressor and any other HVAC gap). The licence and credit ship with the symbols. |
| 3 | Where graphics are stored | **In the site setup** (`graphics` in the config file), so they are checked like everything else, exported with the setup (main spec 5.1.1: graphic bindings export as one file), backed up, and edited by admins (main spec 7). |

## 9A: model, library, rendering and tabs

### The model

A graphic has a name, display name, kind (`site`, `room` or `system`), an optional room (`space`), a size (a drawing area in its own units), an optional tab (`tab: {order}`), and items:

- **symbol:** a library symbol at a place and size, with bindings by point name: `run` (On or Off), `value` (a value symbol's reading or a level's fill), `fault`, and `command` (a writable point opened by tapping). Offline comes from the bound points' quality; Manual from a hand-mode override on the command point; Fault also from an active alarm on a bound point.
- **value:** a live reading with units (`--` and the unit when there is no data).
- **pipe** and **duct:** a polyline with a colour rule: *follows a symbol*, *value bands* (up to four, with hysteresis), or *proportional fill* (0 to 100 %). Flow animates only while the rule shows flow. No data: dashed grey.
- **room:** a rectangle with a name, live values and a value-band rule for its border and fill; tapping opens the room's tab.
- **text:** a label.

Checks: names unique, bound points exist, a `command` binding is a writable point, a room item's room exists, tab orders unique.

### Rendering (web app and HMI)

- SVG in the page, no scripts in symbols; animation by CSS (spinning fans and pumps, flowing pipes and ducts, level fills).
- **States** with the decided priority: Offline, then Fault, then Manual, then On or Off. Colour is never the only signal: Offline is dashed, Fault flashes with a badge, Manual has a hand badge, On animates.
- **Tap to control:** a symbol with a `command` binding opens the same command panel as the Points page (milestone 7A: limits, confirm, hand mode, audit). On the HMI it needs the PIN.

### Tabs (main spec 6.5)

- A **Graphics** page in the web app and the **Graphics** tab on the HMI show a tab strip: **Site** first, then the room and section graphics in their tab order.
- **Starting graphics:** a site with no graphics gets them made from its setup: a Site graphic with every room as a rectangle (its temperature and colour rule when it has a temperature point), and one graphic per room with its equipment's symbols bound by role and tags, laid out in rows. An admin can regenerate them, or edit them as JSON until the editor (9B).

## 9B: the editor, custom symbols and SVG import

Planned after 9A.
