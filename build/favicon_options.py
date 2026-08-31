"""Render favicon candidates as a contact sheet, at the sizes a tab actually uses.

The rule that matters here: a mark is judged at 16px on BOTH a light and a dark tab
strip, because that is the only place it is ever seen. Several of these read fine at
64 and turn to mush at 16, which is the point of the sheet.

    python build/favicon_options.py

Writes data/favicon-options.png. Nothing here ships until one is chosen; the winner
gets copied into docs/favicon.svg and rasterised by build/icons.py.
"""
import io
from pathlib import Path
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
GREEN = "#0b6b3a"       # the wordmark's sign green
DARK = "#171c24"        # the current tile
RUST = "#e2674a"        # the site accent
W = "#ffffff"

def sign(inner, bg=GREEN, border=True):
    """A distance-sign tile: the wordmark's own shape, at tab size."""
    b = (f'<rect x="2.5" y="2.5" width="27" height="27" rx="4" fill="none" '
         f'stroke="{W}" stroke-width="2"/>') if border else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            f'<rect width="32" height="32" rx="7" fill="{bg}"/>{b}{inner}</svg>')

def plain(inner, bg=DARK):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            f'<rect width="32" height="32" rx="7" fill="{bg}"/>{inner}</svg>')

OPTIONS = [
    # --- the sign family: same idea as the wordmark ---
    ("Sign, white dot", sign(f'<circle cx="16" cy="16" r="5.5" fill="{W}"/>')),
    ("Sign, dot off-centre", sign(f'<circle cx="20.5" cy="12" r="5" fill="{W}"/>')),
    ("Sign, arrow", sign(f'<path d="M9 16h12m-4.5-5 5 5-5 5" fill="none" stroke="{W}" '
                         f'stroke-width="2.6" stroke-linecap="round" '
                         f'stroke-linejoin="round"/>')),
    ("Sign, road and dot", sign(
        f'<path d="M6 24h20" stroke="{W}" stroke-width="2.4" stroke-linecap="round"/>'
        f'<circle cx="16" cy="12" r="4" fill="{W}"/>')),
    ("Sign, no border, dot", sign(f'<circle cx="16" cy="16" r="6.5" fill="{W}"/>',
                                  border=False)),
    ("Sign, rust dot", sign(f'<circle cx="16" cy="16" r="5.5" fill="{RUST}"/>')),
    ("Sign, vanishing road", sign(
        f'<path d="M4 27 L14 11 M28 27 L18 11" stroke="{W}" stroke-width="2.2" '
        f'stroke-linecap="round"/>')),
    ("Sign, single bar", sign(f'<rect x="8" y="14" width="16" height="4" rx="2" '
                              f'fill="{W}"/>')),

    # --- the road-and-dot family: the current mark and variants ---
    ("Current: road + dot", plain(
        f'<path d="M3 25.5 Q10 27 16 24.5 T29 22" fill="none" stroke="#5b6673" '
        f'stroke-width="2.4" stroke-linecap="round"/>'
        f'<circle cx="12.5" cy="10" r="3.6" fill="{RUST}"/>')),
    ("Road + dot, brighter road", plain(
        f'<path d="M3 25.5 Q10 27 16 24.5 T29 22" fill="none" stroke="{W}" '
        f'stroke-width="2.6" stroke-linecap="round"/>'
        f'<circle cx="13" cy="10" r="4.6" fill="{RUST}"/>')),
    ("Road + dot, green tile", plain(
        f'<path d="M3 25.5 Q10 27 16 24.5 T29 22" fill="none" stroke="{W}" '
        f'stroke-width="2.6" stroke-linecap="round"/>'
        f'<circle cx="13" cy="10" r="4.6" fill="{W}"/>', bg=GREEN)),
    ("Road along the bottom edge", plain(
        f'<path d="M2 27h28" stroke="{W}" stroke-width="3" stroke-linecap="round"/>'
        f'<circle cx="16" cy="12" r="5" fill="{RUST}"/>')),

    # --- distance and emptiness ---
    ("Dot with range ring", plain(
        f'<circle cx="16" cy="16" r="10" fill="none" stroke="#5b6673" '
        f'stroke-width="2" stroke-dasharray="3 3"/>'
        f'<circle cx="16" cy="16" r="4.5" fill="{RUST}"/>')),
    ("Dot alone, rust", plain(f'<circle cx="16" cy="16" r="8" fill="{RUST}"/>')),
    ("Dot alone, green tile", plain(f'<circle cx="16" cy="16" r="8" fill="{W}"/>',
                                    bg=GREEN)),
    ("Road ending in a dot", plain(
        f'<path d="M4 28 Q10 20 16 17" fill="none" stroke="{W}" stroke-width="2.6" '
        f'stroke-linecap="round"/><circle cx="19" cy="15" r="4.5" fill="{RUST}"/>')),
    ("Two roads, gap between", plain(
        f'<path d="M2 8h11 M19 24h11" stroke="{W}" stroke-width="2.8" '
        f'stroke-linecap="round"/><circle cx="16" cy="16" r="4" fill="{RUST}"/>')),
    ("Sign post", plain(
        f'<rect x="15" y="16" width="2.5" height="13" rx="1" fill="#5b6673"/>'
        f'<rect x="4" y="4" width="24" height="13" rx="2.5" fill="{GREEN}"/>'
        f'<circle cx="16" cy="10.5" r="3.2" fill="{W}"/>')),
]

SIZES = [16, 20, 32, 64]
PAD, GAP, ROWH = 14, 18, 78
LABELW = 210


def main():
    shots = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for _, svg in OPTIONS:
            row = {}
            for s in SIZES:
                pg = b.new_page(viewport={"width": s, "height": s},
                                device_scale_factor=4)
                pg.set_content(f'<body style="margin:0">'
                               f'<div style="width:{s}px;height:{s}px">{svg}</div>')
                pg.wait_for_timeout(40)
                im = Image.open(io.BytesIO(pg.screenshot(omit_background=True)))
                row[s] = im.resize((s, s), Image.LANCZOS)
                pg.close()
            shots.append(row)
        b.close()

    stripw = sum(SIZES) + GAP * len(SIZES) + PAD
    width = LABELW + stripw * 2 + PAD * 2
    height = PAD * 2 + ROWH * len(OPTIONS) + 30
    sheet = Image.new("RGB", (width, height), "#ffffff")
    d = ImageDraw.Draw(sheet)

    x_light = LABELW
    x_dark = LABELW + stripw
    d.rectangle([x_dark, 0, x_dark + stripw, height], fill="#202124")
    d.text((x_light + 8, 8), "light tab strip", fill="#5b6673")
    d.text((x_dark + 8, 8), "dark tab strip", fill="#c9ced6")

    for i, ((name, _), row) in enumerate(zip(OPTIONS, shots)):
        y = PAD + 30 + i * ROWH
        d.text((PAD, y + ROWH // 2 - 6), f"{i + 1:2}. {name}", fill="#11161d")
        for base, ink in ((x_light, "#f2f3f5"), (x_dark, "#2b2f36")):
            cx = base + PAD
            d.rectangle([base, y, base + stripw, y + ROWH - 6], fill=ink)
            for s in SIZES:
                im = row[s]
                sheet.paste(im, (cx, y + (ROWH - 6 - s) // 2), im)
                cx += s + GAP

    out = ROOT / "data" / "favicon-options.png"
    out.parent.mkdir(exist_ok=True)
    sheet.save(out)
    print(f"  -> {out}  ({width}x{height})")


if __name__ == "__main__":
    main()
