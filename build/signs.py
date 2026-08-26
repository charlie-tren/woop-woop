"""Wordmark as an Australian road sign, in a few treatments.

Overpass is the face throughout: it is derived from highway signage, so the joke and
the subject are the same thing. Each is shown at header size AND at the size it would
appear in a tab or a card, because a sign that needs its border read is a sign that
only works large.
"""
import io, sys
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

FONT_CSS = ("https://fonts.googleapis.com/css2?family=Overpass:wght@600;700"
            "&family=Overpass+Mono:wght@600&display=block")

# Australian conventions: green + white for distance and direction, white + black for
# street blades, brown for tourist routes.
SIGNS = {
    "Distance sign": """
      <div class="sign green">
        <div class="inner"><span class="t">WOOP WOOP</span></div>
      </div>""",
    "Distance sign, with the km": """
      <div class="sign green">
        <div class="inner row"><span class="t">WOOP WOOP</span><span class="km">372</span></div>
      </div>""",
    "Fingerboard, arrow": """
      <div class="sign green">
        <div class="inner row"><span class="t">WOOP WOOP</span><span class="arr">&#10230;</span></div>
      </div>""",
    "Street blade": """
      <div class="sign blade">
        <div class="inner"><span class="t">WOOP WOOP</span></div>
      </div>""",
    "Tourist brown": """
      <div class="sign brown">
        <div class="inner"><span class="t">WOOP WOOP</span></div>
      </div>""",
    "Stacked, two lines": """
      <div class="sign green tall">
        <div class="inner col"><span class="t">WOOP</span><span class="t">WOOP</span></div>
      </div>""",
}

CSS = """
  body{margin:0;background:#161b22;color:#e6edf3;
       font:15px/1.5 ui-sans-serif,system-ui,'Segoe UI',sans-serif}
  .row-wrap{display:flex;align-items:center;gap:34px;padding:20px 24px;
            border-bottom:1px solid #283039}
  .sign{display:inline-block;border-radius:6px;padding:4px}
  .sign .inner{border:2px solid currentColor;border-radius:4px;padding:5px 12px;
               display:flex;align-items:center;gap:14px}
  .sign .inner.col{flex-direction:column;gap:0;line-height:.98}
  .t{font-family:'Overpass';font-weight:700;letter-spacing:.02em;white-space:nowrap}
  .km{font-family:'Overpass Mono';font-weight:600;opacity:.95}
  .arr{font-weight:400;line-height:1}
  .green{background:#0b6b3a;color:#fff}
  .blade{background:#fff;color:#111}
  .brown{background:#5a4632;color:#fff}
  .big .t{font-size:26px} .big .km{font-size:22px} .big .arr{font-size:26px}
  .small{transform-origin:left center}
  .small .t{font-size:12px} .small .km{font-size:10px} .small .arr{font-size:12px}
  .small .inner{border-width:1.5px;padding:3px 7px;gap:8px}
  .label{margin-left:auto;color:#5b6673;font-size:12px;white-space:nowrap}
  .tag{color:#8b949e;font-size:13.5px}
"""

HTML = """<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="{css}">
<style>{style}</style>
<div class="row-wrap">
  <div class="big">{markup}</div>
  <div class="small">{markup}</div>
  <div class="tag">The emptiest place within reach.</div>
  <div class="label">{name}</div>
</div>"""


def main(out="data/signs.png"):
    shots = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, markup in SIGNS.items():
            pg = b.new_page(viewport={"width": 1180, "height": 120})
            pg.set_content(HTML.format(css=FONT_CSS, style=CSS, markup=markup,
                                       name=name))
            pg.wait_for_function("async () => { await document.fonts.ready;"
                                 " return document.fonts.check('26px Overpass'); }",
                                 timeout=15000)
            pg.wait_for_timeout(300)
            shots.append(Image.open(io.BytesIO(
                pg.locator(".row-wrap").screenshot())))
            print("  " + name)
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
