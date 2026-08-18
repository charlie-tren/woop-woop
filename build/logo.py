"""Build the wordmark as a road sign, with the lettering converted to outlines.

SVG <text> renders in whatever face the VIEWER has, so a wordmark that relies on it is
a wordmark that changes shape on someone else's machine. Converting the glyphs to paths
makes the file self-contained: no web font request, no layout shift, no fallback.

Overpass is the face because it is derived from highway signage - the joke and the
subject are the same thing.
"""
import sys, urllib.request
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen

TTF_URL = ("https://fonts.gstatic.com/s/overpass/v19/"
           "qFda35WCmI96Ajtm83upeyoaX6QPnlo6G_TrOQ.ttf")
TEXT = "WOOP WOOP"
CAP_PX = 34.0          # cap height of the lettering in the output
TRACKING = 0.02        # em, matching the sign's slightly open spacing
PAD_X, PAD_Y = 20, 13  # inside the white keyline
BORDER, INSET, RADIUS = 2.6, 4.0, 3.0
GREEN, WHITE = "#0b6b3a", "#ffffff"
OUT = Path(__file__).resolve().parent.parent / "docs" / "logo.svg"
CACHE = Path(__file__).resolve().parent.parent / "data" / "overpass700.ttf"


def outlines(font, text, scale):
    """One path string for the whole wordmark, glyphs advanced by their own widths."""
    cmap = font.getBestCmap()
    gs = font.getGlyphSet()
    upem = font["head"].unitsPerEm
    d, x = [], 0.0
    for ch in text:
        name = cmap.get(ord(ch))
        if name is None:
            raise SystemExit(f"no glyph for {ch!r}")
        pen = SVGPathPen(gs)
        # y is flipped: font units go up, SVG goes down.
        gs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, 0)))
        seg = pen.getCommands()
        if seg:
            d.append(seg)
        x += gs[name].width * scale + TRACKING * upem * scale
    return " ".join(d), x - TRACKING * upem * scale


def main():
    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(urllib.request.urlopen(TTF_URL, timeout=60).read())
        print(f"  fetched {CACHE.name}")
    font = TTFont(CACHE)

    # Scale from the real cap height rather than the em square, so the lettering is the
    # size asked for instead of the size the designer's metrics imply.
    cap = font["OS/2"].sCapHeight if hasattr(font["OS/2"], "sCapHeight") else None
    if not cap:
        bp = BoundsPen(font.getGlyphSet())
        font.getGlyphSet()["H"].draw(bp)
        cap = bp.bounds[3]
    scale = CAP_PX / cap

    path, width = outlines(font, TEXT, scale)

    inner_w = width + 2 * PAD_X
    inner_h = CAP_PX + 2 * PAD_Y
    w = inner_w + 2 * INSET
    h = inner_h + 2 * INSET
    # Baseline sits at the bottom of the caps.
    ty = INSET + PAD_Y + CAP_PX

    svg = f'''<!-- Woop Woop: an Australian green distance sign. Lettering is Overpass
     700 converted to outlines, so the mark cannot be reshaped by a viewer's fonts.
     Regenerate with build/logo.py; do not hand-edit the path data. -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.1f} {h:.1f}"
     preserveAspectRatio="xMinYMid meet" role="img" aria-label="Woop Woop">
  <rect x="0" y="0" width="{w:.1f}" height="{h:.1f}" rx="{RADIUS + INSET:.1f}"
        fill="{GREEN}"/>
  <rect x="{INSET:.1f}" y="{INSET:.1f}" width="{inner_w:.1f}" height="{inner_h:.1f}"
        rx="{RADIUS:.1f}" fill="none" stroke="{WHITE}" stroke-width="{BORDER}"/>
  <g transform="translate({INSET + PAD_X:.2f} {ty:.2f})" fill="{WHITE}">
    <path d="{path}"/>
  </g>
</svg>
'''
    OUT.write_text(svg, encoding="utf-8")
    # No width/height attributes on purpose: they win over CSS, so an SVG carrying
    # them refuses to scale to its container and overflows the header.
    print(f"  {OUT.name}: viewBox {w:.0f}x{h:.0f} (aspect {w/h:.2f}), "
          f"{len(svg):,} bytes")


if __name__ == "__main__":
    main()
