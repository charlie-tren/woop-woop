"""Screenshot the same view over every candidate basemap, then stack them for comparison.

The overlay and the viewport are held identical across all of them, so the only thing
that differs in the sheet is the thing being chosen.
"""
import io, sys
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

URL = "https://charlietrenorden.com/furthest/"
VIEW = {"width": 760, "height": 520}
CENTRE = [-27.33, 152.62]   # the Wivenhoe answer, where empty country meets settled
ZOOM = 10

BASES = [
    ("OSM standard (current)",
     "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
    ("Esri World Imagery",
     "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/"
     "MapServer/tile/{z}/{y}/{x}"),
    ("Esri Shaded Relief",
     "https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/"
     "MapServer/tile/{z}/{y}/{x}"),
    ("Esri World Terrain",
     "https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/"
     "MapServer/tile/{z}/{y}/{x}"),
    ("CARTO Positron",
     "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"),
    ("CARTO Dark Matter",
     "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"),
    ("CARTO Voyager",
     "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"),
    ("OpenTopoMap",
     "https://tile.opentopomap.org/{z}/{x}/{y}.png"),
    ("No basemap (field only)", None),
]

SWAP = """([url, centre, zoom]) => {
  map.eachLayer(l => { if (l instanceof L.TileLayer) map.removeLayer(l); });
  if (url) L.tileLayer(url, {maxZoom: 17}).addTo(map);
  map.setView(centre, zoom, {animate: false});
  return true;
}"""


def main(out="data/basemaps.png"):
    shots = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport=VIEW)
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_timeout(2500)
        # The side panel is not what is being compared; give the map the full frame.
        pg.evaluate("() => { document.querySelector('aside').style.display='none';"
                    "document.querySelector('header').style.display='none';"
                    "map.invalidateSize(); }")
        for name, url in BASES:
            pg.evaluate(SWAP, [url, CENTRE, ZOOM])
            pg.wait_for_timeout(3800)          # tiles, not layout
            shots.append((name, Image.open(io.BytesIO(
                pg.locator("#map").screenshot()))))
            print(f"  shot {name}")
        b.close()

    cols, pad, cap = 3, 10, 26
    w, h = shots[0][1].size
    rows = (len(shots) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (w + pad) + pad,
                              rows * (h + cap + pad) + pad), "#0d1117")
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("segoeuib.ttf", 15)
    except Exception:
        font = ImageFont.load_default()
    for i, (name, im) in enumerate(shots):
        x = pad + (i % cols) * (w + pad)
        y = pad + (i // cols) * (h + cap + pad)
        d.text((x + 2, y + 4), name, fill="#e6edf3", font=font)
        sheet.paste(im, (x, y + cap))
    sheet.save(out)
    print("->", out, sheet.size)


if __name__ == "__main__":
    main(*sys.argv[1:])
