"""Build the shipped symbol library (milestone 9A, decision 025): web/public/symbols/<id>.svg.

Equinor's engineering-symbols (MIT, https://github.com/equinor/engineering-symbols) are taken from
a checkout given on the command line, recoloured to `currentColor` (the BMS colours them by state
and theme), stripped of their connector annotations, and centred on a 64 x 64 viewBox. Symbols
Equinor does not have are drawn here in the same thin-line style.

    python tools/build_symbols.py /path/to/engineering-symbols
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = json.loads((ROOT / "backend/src/devine_bms/graphics/library.json").read_text())
OUT = ROOT / "web/public/symbols"
LINE = 'fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round"'

# Drawn for Devine BMS, 64 x 64, in Equinor's thin-line style.
DEVINE = {
    "compressor": f'<g {LINE}><circle cx="32" cy="32" r="20"/><path d="M15 21 L49 26 M15 43 L49 38"/></g>',
    "coil-cooling": f'<g {LINE}><path d="M6 32 H14 L18 22 L24 42 L30 22 L36 42 L42 22 L48 42 L50 32 H58"/>'
                    f'<path d="M26 12 L32 6 L38 12 M32 6 V18 M26 52 L32 58 L38 52 M32 58 V46"/></g>',
    "heat-recovery": f'<g {LINE}><circle cx="32" cy="32" r="22"/><path d="M32 10 V54 M10 32 H54 M16.4 16.4 L47.6 47.6 '
                     f'M47.6 16.4 L16.4 47.6"/><circle cx="32" cy="32" r="4"/></g>',
    "meter": f'<g {LINE}><rect x="12" y="14" width="40" height="36" rx="2"/><rect x="18" y="20" width="28" height="12"/>'
             f'<path d="M20 42 H44"/><circle cx="32" cy="42" r="0.8"/></g>',
    "light": f'<g {LINE}><path d="M24 40 C16 34 18 14 32 14 C46 14 48 34 40 40 V46 H24 Z"/><path d="M26 50 H38 M28 54 H36"/>'
             f'<path d="M28 36 L32 28 L36 36"/></g>',
    "fan-axial": f'<g {LINE}><circle cx="32" cy="32" r="22"/><path d="M32 32 C26 26 26 14 32 12 C38 14 38 26 32 32 Z '
                 f'M32 32 C38 38 38 50 32 52 C26 50 26 38 32 32 Z"/><circle cx="32" cy="32" r="2.5"/></g>',
    "door": f'<g {LINE}><path d="M14 52 H50 M18 52 V14 H40 V52"/><path d="M40 52 L54 42 V8 L40 14"/><circle cx="36" cy="34" r="1"/></g>',
}


def equinor(source_dir: Path, code: str) -> str:
    raw = (source_dir / "package/src/svg" / f"{code}.svg").read_text()
    raw = re.sub(r'<g id="Annotations">.*?</g>', "", raw, flags=re.S)
    view = re.search(r'viewBox="([^"]+)"', raw).group(1)
    inner = re.search(r"<svg[^>]*>(.*)</svg>", raw, flags=re.S).group(1)
    inner = inner.replace('fill="#231f20"', 'fill="currentColor"')
    return (f'<svg x="4" y="4" width="56" height="56" viewBox="{view}" preserveAspectRatio="xMidYMid meet" '
            f'fill="currentColor">{inner.strip()}</svg>')


def main(source_dir: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for sym in LIBRARY:
        kind, _, code = sym["source"].partition(":")
        body = equinor(source_dir, code) if kind == "equinor" else DEVINE[sym["id"]]
        if sym.get("text"):
            body += (f'<text x="32" y="33" text-anchor="middle" dominant-baseline="middle" font-family="sans-serif" '
                     f'font-size="20" fill="currentColor">{sym["text"]}</text>')
        credit = (f"Equinor engineering-symbols {code}, MIT licence (LICENSE-equinor.txt)" if kind == "equinor"
                  else "Devine BMS")
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
               f"<!-- {sym['dis']}: {credit} -->{body}</svg>\n")
        (OUT / f"{sym['id']}.svg").write_text(svg)
    print(f"wrote {len(LIBRARY)} symbols to {OUT}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
