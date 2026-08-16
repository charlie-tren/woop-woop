"""Render the wordmark in a range of typefaces, in situ on the page chrome.

Shown at the real header size with the real tagline underneath, plus a small copy,
because a wordmark judged large and alone always looks better than it does in a header.
"""
import io, sys
from PIL import Image
from playwright.sync_api import sync_playwright

NAME = "Woop Woop"
TAGLINE = "The middle of nowhere, precisely located."

# Spanning registers deliberately: condensed grotesque, geometric, monospace,
# editorial serif, display, humanist.
FONTS = [
    ("Oswald", 500, "condensed grotesque - map label"),
    ("Archivo Narrow", 700, "condensed, workmanlike"),
    ("Bebas Neue", 400, "display caps, poster"),
    ("Anton", 400, "heavy display, loud"),
    ("Space Grotesk", 600, "geometric with quirks"),
    ("Outfit", 600, "clean geometric, neutral"),
    ("Sora", 600, "technical geometric"),
    ("Bricolage Grotesque", 700, "characterful grotesque"),
    ("JetBrains Mono", 600, "monospace, coordinates"),
    ("IBM Plex Mono", 600, "monospace, softer"),
    ("Fraunces", 600, "editorial serif, warm"),
    ("Instrument Serif", 400, "high-contrast serif"),
    ("DM Serif Display", 400, "classical display serif"),
    ("Playfair Display", 700, "elegant, bookish"),
]

CSS_FAMILIES = "&family=".join(
    f"{n.replace(' ', '+')}:wght@{w}" if n not in
    ("Bebas Neue", "Anton", "Instrument Serif", "DM Serif Display")
    else n.replace(" ", "+") for n, w, _ in FONTS)

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
