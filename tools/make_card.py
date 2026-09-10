"""Renders docs/og-card.png, the 1200x630 share card.

    python tools/make_card.py

WHY THIS EXISTS. The page had no card tags at all, so a share unfurled as a bare
link. Honest, unlike declaring `summary_large_image` with no image - which is
what a sweep of the estate on 10/09/2026 found on CFA Companion - but a link with
no preview still reads as dead next to carded ones.

TWO RULES, both from the siblings (hub/tools/make_cards.py, dcf-studio).

1. PALETTE AND COPY PARSED FROM THE PAGE, never typed here. A hand-copied
   palette is how statecraft's card kept an old accent through two redesigns.
   This page pins `data-theme="dark"` on <html>, so the dark block is what a
   visitor actually sees and the card is built from it.

2. NO WEBFONT TO ASSERT. The siblings refuse to shoot unless the site's face has
   loaded, because a card in the fallback advertises a design the site does not
   have. This page specifies `ui-sans-serif, system-ui` and nothing else, so
   there is no correct face to demand - the card takes the shooting machine's
   system sans, which is faithful to what the CSS actually asks for. Worth
   stating rather than leaving as a silent gap in the checks.
"""

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
PAGE = ROOT / "docs" / "index.html"
OUT = ROOT / "docs" / "og-card.png"
SCRATCH = HERE / "_card_scratch.html"

WIDTH, HEIGHT = 1200, 630
PROP = re.compile(r"--([a-z0-9-]+):\s*([^;]+);")


def tokens():
    """The dark block, which is the theme <html> pins."""
    css = PAGE.read_text(encoding="utf-8")
    start = css.index(':root[data-theme="dark"]')
    block = css[start:css.index("}", start)]
    found = {k: v.strip() for k, v in PROP.findall(block)}
    missing = [k for k in ("bg", "panel", "ink", "muted", "line", "accent") if k not in found]
    if missing:
        raise SystemExit(f"the dark block has no {', '.join(missing)}; the card "
                         f"cannot be built from it")
    return found


def copy():
    """Title and description, read out of the page's own head."""
    html = PAGE.read_text(encoding="utf-8")
    title = re.search(r"<title>([^<]+)</title>", html)
    desc = re.search(r'<meta name="description" content="([^"]+)"', html)
    if not title or not desc:
        raise SystemExit("docs/index.html has no <title> or no meta description; "
                         "the card would have to invent its own wording")
    return title.group(1), desc.group(1)


def page_html(t, name, lede):
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ width: {WIDTH}px; height: {HEIGHT}px; }}
  body {{
    background: {t['bg']};
    color: {t['ink']};
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    padding: 76px 84px 68px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    position: relative;
  }}
  .eyebrow {{
    font-size: 19px;
    font-weight: 600;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: {t['accent']};
  }}
  h1 {{
    margin-top: 20px;
    font-size: 100px;
    font-weight: 600;
    letter-spacing: -0.03em;
    line-height: 1;
  }}
  .lede {{
    margin-top: 28px;
    margin-bottom: 92px;
    max-width: 900px;
    color: {t['muted']};
    font-size: 31px;
    line-height: 1.38;
  }}
  .foot {{
    position: absolute;
    left: 84px;
    right: 84px;
    bottom: 68px;
    padding-top: 28px;
    border-top: 1px solid {t['line']};
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    font-size: 20px;
    color: {t['muted']};
  }}
</style></head>
<body>
  <p class="eyebrow">Australia</p>
  <h1>{name}</h1>
  <p class="lede">{lede}</p>
  <div class="foot"><span>charlietrenorden.com/woop-woop</span><span>Charlie Trenorden</span></div>
</body></html>"""


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: pip install playwright && "
              "playwright install chromium", file=sys.stderr)
        return 1
    t = tokens()
    name, lede = copy()
    SCRATCH.write_text(page_html(t, name, lede), encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT},
                                    device_scale_factor=1)
            page.goto(SCRATCH.as_uri())
            page.screenshot(path=str(OUT))
            browser.close()
    finally:
        SCRATCH.unlink(missing_ok=True)
    size = OUT.stat().st_size
    print(f"wrote {OUT}")
    print(f"  {WIDTH}x{HEIGHT}, {size:,} bytes ({size / 1024:.1f} KiB)")
    print(f"  dark palette from docs/index.html: bg {t['bg']}, accent {t['accent']}")
    print(f"  lede: {lede}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
