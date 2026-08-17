"""Render the wordmark in a range of typefaces, in situ on the page chrome.

Shown at the real header size with the real tagline underneath, plus a small copy,
because a wordmark judged large and alone always looks better than it does in a header.
"""
import io, sys
from PIL import Image
from playwright.sync_api import sync_playwright

NAME = "Woop Woop"
TAGLINE = "The emptiest place you can get to."

# Spanning registers deliberately: condensed grotesque, geometric, monospace,
# editorial serif, display, humanist.
FONTS = [
    # Deliberately spanning genres rather than weights. The first pass was fourteen
    # respectable sans and serifs, which is one axis explored fourteen times.
    ("Overpass", 700, "highway signage - literally built from road signs"),
    ("Big Shoulders Display", 700, "civic signage, condensed"),
    ("Fjalla One", 400, "ultra condensed, newsstand"),
    ("Archivo Black", 400, "brutalist slab of a sans"),
    ("Syne", 700, "art-school grotesque, odd widths"),
    ("Alfa Slab One", 400, "fairground slab"),
    ("Rye", 400, "wood type, western"),
    ("Special Elite", 400, "typewriter, field notes"),
    ("Courier Prime", 700, "clean typewriter"),
    ("Silkscreen", 700, "bitmap, GPS unit"),
    ("Press Start 2P", 400, "8-bit"),
    ("Orbitron", 700, "techno, instrument panel"),
    ("Saira Stencil One", 400, "stencil, crate-stamped"),
    ("Permanent Marker", 400, "marker pen"),
    ("Amatic SC", 700, "hand-drawn, tall and thin"),
    ("Cabin Sketch", 700, "sketched, outdoorsy"),
    ("Bangers", 400, "comic shout"),
    ("Fredoka", 600, "rounded, friendly"),
    ("Poiret One", 400, "art deco, fine"),
    ("Lobster", 400, "script, roadside diner"),
]

# Single-weight families 404 if a wght axis is requested, which silently drops the
# whole stylesheet and renders every row in the fallback face.
SINGLE_WEIGHT = {"Fjalla One", "Archivo Black", "Alfa Slab One", "Rye",
                 "Special Elite", "Press Start 2P", "Saira Stencil One",
                 "Permanent Marker", "Bangers", "Poiret One", "Lobster"}
CSS_FAMILIES = "&family=".join(
    n.replace(" ", "+") if n in SINGLE_WEIGHT
    else f"{n.replace(' ', '+')}:wght@{w}" for n, w, _ in FONTS)

HTML = """<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family={fams}&display=block">
<style>
  body{{margin:0;background:#161b22;color:#e6edf3;
       font:15px/1.5 ui-sans-serif,system-ui,'Segoe UI',sans-serif}}
  .row{{display:flex;align-items:center;gap:26px;padding:16px 22px;
        border-bottom:1px solid #283039}}
  .mark{{font-family:'{fam}';font-weight:{wt};font-size:34px;letter-spacing:-.01em;
         white-space:nowrap}}
  .small{{font-family:'{fam}';font-weight:{wt};font-size:17px;white-space:nowrap;
          opacity:.95}}
  .tag{{color:#8b949e;font-size:13.5px}}
  .note{{margin-left:auto;color:#5b6673;font-size:12px;text-align:right;
         white-space:nowrap}}
</style>
<div class="row">
  <div class="mark">{name}</div>
  <div class="small">{name}</div>
  <div class="tag">{tagline}</div>
  <div class="note">{fam} {wt}<br>{desc}</div>
</div>"""


def main(out="data/fonts.png"):
    shots = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for fam, wt, desc in FONTS:
            pg = b.new_page(viewport={"width": 1180, "height": 90})
            pg.set_content(HTML.format(fams=CSS_FAMILIES, fam=fam, wt=wt, name=NAME,
                                       tagline=TAGLINE, desc=desc))
            try:
                pg.wait_for_function(
                    "async () => { await document.fonts.ready;"
                    " return document.fonts.check('34px \"%s\"'); }" % fam,
                    timeout=15000)
            except Exception:
                print(f"  WARNING {fam} did not load - shown in fallback")
            pg.wait_for_timeout(400)
            shots.append(Image.open(io.BytesIO(pg.locator(".row").screenshot())))
            print(f"  {fam}")
            pg.close()
        b.close()

    w = max(s.width for s in shots)
    sheet = Image.new("RGB", (w, sum(s.height for s in shots)), "#161b22")
    y = 0
    for s in shots:
        sheet.paste(s, (0, y)); y += s.height
    sheet.save(out)
    print("->", out, sheet.size)


if __name__ == "__main__":
    main(*sys.argv[1:])
