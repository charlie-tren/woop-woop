"""Render favicon.svg to the real PNG and ICO files a browser and Google will use.

Rendered from the SVG rather than drawn twice, so the raster and vector marks cannot
drift apart. An SVG-only favicon leaves a BLANK tab in Safari and older Chrome, and a
`data:` URI leaves a generic globe in Google results - hence real files.
"""
import io, sys
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

SITE = Path(__file__).resolve().parent.parent / "docs"
SIZES = [16, 32, 48, 180, 192]


def main():
    svg = (SITE / "favicon.svg").read_text(encoding="utf-8")
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for s in SIZES:
            pg = b.new_page(viewport={"width": s, "height": s},
                            device_scale_factor=1)
            pg.set_content(
                f'<body style="margin:0">'
                f'<div style="width:{s}px;height:{s}px">{svg}</div>')
            pg.wait_for_timeout(60)
            out[s] = Image.open(io.BytesIO(pg.screenshot(omit_background=True)))
            pg.close()
        b.close()

    out[192].save(SITE / "favicon-192.png")
    out[48].save(SITE / "favicon-48.png")
    out[180].save(SITE / "apple-touch-icon.png")
    # apple-touch-icon must not be transparent - iOS renders alpha as BLACK.
    flat = Image.new("RGB", out[180].size, "#0b6b3a")
    flat.paste(out[180], mask=out[180].split()[-1])
    flat.save(SITE / "apple-touch-icon.png")
    out[48].save(SITE / "favicon.ico",
                 sizes=[(16, 16), (32, 32), (48, 48)])
    for f in ("favicon-192.png", "favicon-48.png", "apple-touch-icon.png",
              "favicon.ico"):
        print(f"  {f:24} {(SITE / f).stat().st_size:>7,} bytes")

    # A contact sheet at TRUE tab size, on both tab strips, because that is the only
    # place this mark is ever judged.
    sheet = Image.new("RGB", (240, 60), "#ffffff")
    sheet.paste(Image.new("RGB", (240, 30), "#f2f3f5"), (0, 0))
    sheet.paste(Image.new("RGB", (240, 30), "#202124"), (0, 30))
    for i, s in enumerate((16, 32)):
        for row, _ in enumerate((0, 1)):
            im = out[s]
            sheet.paste(im, (20 + i * 60, row * 30 + (30 - s) // 2), im)
    sheet.resize((960, 240), Image.NEAREST).save(SITE.parent / "data" / "favicon-check.png")
    print("  contact sheet -> data/favicon-check.png")


if __name__ == "__main__":
    main()
