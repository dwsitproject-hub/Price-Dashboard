"""Generate every AASTAR icon variant from one artwork definition (64-unit grid).

    python docs/brand/make_icons.py          # writes the SVGs below; PNG/ICO export: see docs/brand/README.md

Outputs:
  docs/brand/aastar-app-icon.svg        master app icon (rounded tile), any size >= 48 px
  docs/brand/aastar-mark.svg            same artwork (the in-app LogoMark in frontend/src/components/Logo.tsx
                                        mirrors these paths — update both together)
  docs/brand/aastar-logo.svg / -dark    lockups: mark + "AASTAR / PRICE INTELLIGENCE"
  docs/brand/aastar-icon-maskable.svg   full-bleed, content inside the 80 % safe zone (Android / PWA)
  docs/brand/aastar-icon-apple.svg      full-bleed, iOS rounds the corners itself
  frontend/public/favicon.svg           simplified 32-unit drawing for 16–32 px (browser tab)
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "docs" / "brand"

# ── Palette (tokens.css brand / accent) ──────────────────────────────────────
TILE = [("0", "#18977E"), (".55", "#0C5F53"), ("1", "#06322C")]
DROP = [("0", "#FFDB7D"), (".55", "#F1AD2C"), ("1", "#C97C06")]
LINE = "#073A33"
STAR = "#FFD36B"

# ── Artwork on the 64 grid ───────────────────────────────────────────────────
DROP_PATH = "M30 10.5C25.6 17.2 15 28 15 38a15 15 0 0 0 30 0C45 28 34.4 17.2 30 10.5z"
GLOSS_PATH = "M21.2 36.5c0-5 2.6-10.2 5.8-14.9"
PRICE_PATH = "M21.2 44.2l5.2-5.2 4.4 3.2 6.8-9.6"
STAR_PATH = ("M49.5 7c.8 5.2 2.3 6.7 7.5 7.5-5.2.8-6.7 2.3-7.5 7.5-.8-5.2-2.3-6.7-7.5-7.5"
             "5.2-.8 6.7-2.3 7.5-7.5z")


def _grad(gid: str, stops, x2="1", y2="1") -> str:
    s = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in stops)
    return f'<linearGradient id="{gid}" x1="0" y1="0" x2="{x2}" y2="{y2}">{s}</linearGradient>'


def defs(p: str = "aa") -> str:
    return ("<defs>" + _grad(f"{p}-tile", TILE) + _grad(f"{p}-drop", DROP, x2="0") +
            f'<radialGradient id="{p}-light" cx="14" cy="8" r="44" gradientUnits="userSpaceOnUse">'
            '<stop offset="0" stop-color="#fff" stop-opacity=".18"/>'
            '<stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs>')


def tile(p: str = "aa", rounded: bool = True) -> str:
    rx = ' rx="14.4"' if rounded else ""
    edge = ('<rect x=".6" y=".6" width="62.8" height="62.8" rx="13.8" fill="none" '
            'stroke="#fff" stroke-opacity=".10" stroke-width="1.2"/>') if rounded else ""
    return (f'<rect width="64" height="64"{rx} fill="url(#{p}-tile)"/>'
            f'<rect width="64" height="64"{rx} fill="url(#{p}-light)"/>' + edge)


def motif(p: str = "aa") -> str:
    return (f'<path d="{DROP_PATH}" transform="translate(.8 1.4)" fill="#021F1B" fill-opacity=".30"/>'
            f'<path d="{DROP_PATH}" fill="url(#{p}-drop)"/>'
            f'<path d="{GLOSS_PATH}" fill="none" stroke="#fff" stroke-opacity=".55" stroke-width="2.2" '
            'stroke-linecap="round"/>'
            f'<path d="{PRICE_PATH}" fill="none" stroke="{LINE}" stroke-width="3.4" stroke-linecap="round" '
            'stroke-linejoin="round"/>'
            f'<circle cx="37.6" cy="32.6" r="2.9" fill="#fff" stroke="{LINE}" stroke-width="1.8"/>'
            f'<path d="{STAR_PATH}" fill="{STAR}"/>'
            f'<circle cx="55.6" cy="24.4" r="1.3" fill="{STAR}" fill-opacity=".85"/>')


def svg(body: str, view: str = "0 0 64 64") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" role="img" aria-label="AASTAR">'
            f"{body}</svg>\n")


def app_icon() -> str:
    return svg(defs() + tile() + motif())


def full_bleed(scale: float) -> str:
    off = 32 * (1 - scale)
    return svg(defs() + tile(rounded=False) + f'<g transform="translate({off:g} {off:g}) scale({scale:g})">'
               + motif() + "</g>")


def favicon() -> str:
    # Own drawing on a 32 grid: thicker line, no gloss/dot, so it survives 16 px.
    return svg(
        "<defs>" + _grad("f-tile", TILE) + _grad("f-drop", DROP, x2="0") + "</defs>"
        '<rect width="32" height="32" rx="7.2" fill="url(#f-tile)"/>'
        '<path d="M15 4.2C12.4 8.2 6.4 14 6.4 19.6a8.6 8.6 0 0 0 17.2 0C23.6 14 17.6 8.2 15 4.2z" fill="url(#f-drop)"/>'
        f'<path d="M10.4 23.2l2.8-2.8 2.4 1.8 4-5.6" fill="none" stroke="{LINE}" stroke-width="2.4" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="M25.2 3c.5 3.2 1.2 3.9 4.4 4.4-3.2.5-3.9 1.2-4.4 4.4-.5-3.2-1.2-3.9-4.4-4.4'
        f'3.2-.5 3.9-1.2 4.4-4.4z" fill="{STAR}"/>',
        view="0 0 32 32")


def logo(word: str, tag: str) -> str:
    font = "Inter, 'Segoe UI', Roboto, Arial, sans-serif"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 72" role="img" '
            f'aria-label="AASTAR Price Intelligence">{defs()}<g transform="translate(4 4)">{tile()}{motif()}</g>'
            f'<text x="84" y="40" font-family="{font}" font-size="32" font-weight="800" letter-spacing="2" '
            f'fill="{word}">AASTAR</text>'
            f'<text x="86" y="59" font-family="{font}" font-size="11.5" font-weight="600" letter-spacing="3.6" '
            f'fill="{tag}">PRICE INTELLIGENCE</text></svg>\n')


if __name__ == "__main__":
    out = {
        BRAND / "aastar-app-icon.svg": app_icon(),
        BRAND / "aastar-mark.svg": app_icon(),
        BRAND / "aastar-logo.svg": logo("#0B4F45", "#C98510"),
        BRAND / "aastar-logo-dark.svg": logo("#E7EEF0", "#F2B84B"),
        BRAND / "aastar-icon-maskable.svg": full_bleed(0.76),
        BRAND / "aastar-icon-apple.svg": full_bleed(0.86),
        ROOT / "frontend" / "public" / "favicon.svg": favicon(),
    }
    for path, text in out.items():
        path.write_text(text, encoding="utf-8", newline="\n")
        print("wrote", path.relative_to(ROOT))
