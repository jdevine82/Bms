# 026: Graphics editor, custom symbols and imports (milestone 9B)

Date: 6 Nov 2026. Status: decided.

## Decisions

- **The symbol editor draws simple shapes only:** lines, polylines, rectangles, circles and text on the 64 x 64 grid, each in the Off layer, the On layer or both. Anything more elaborate is drawn in a vector tool and imported. A full vector editor would duplicate tools like Inkscape for little gain.
- **One imported SVG background per graphic**, for floor plans exported from CAD, drawn under everything else.
- **Custom symbols and backgrounds live in the site setup**, like the graphics, so they are checked, exported, imported and backed up with it (main spec 6.4 "Storage").
- **Sanitising is done on the server with an allow-list**, and the setup checks refuse any stored SVG that cleaning would change. The browser never shows an SVG the server has not cleaned.
- **The editor is for desktop browsers.** Phones and the HMI view graphics only (main spec 6.4).

## Consequences

- A large background makes the setup file larger (up to 1 MB per graphic).
- A symbol drawn in the editor keeps its shapes in the setup so it can be edited again; an imported one can only be replaced.
