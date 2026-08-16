"""Measure what is actually slow, rather than guessing which half it is."""
import sys, statistics
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731/"

JS_SOLVE = """() => {
  const t = performance.now();
  for (let i = 0; i < 3; i++) solve();
  return (performance.now() - t) / 3;
}"""

JS_ZOOM = """async () => {
  const t = performance.now();
  map.setZoom(map.getZoom() - 1, {animate: false});
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  return performance.now() - t;
}"""

JS_PAN = """async () => {
  const t = performance.now();
  map.panBy([220, 140], {animate: false});
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  return performance.now() - t;
}"""


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_timeout(2500)

        print(f"  overlay image: {pg.evaluate('document.querySelectorAll(\"img.leaflet-image-layer\").length')} layer(s)")
        print(f"  solve()      : {pg.evaluate(JS_SOLVE):7.1f} ms")
        for name, js in (("zoom", JS_ZOOM), ("pan", JS_PAN)):
            xs = [pg.evaluate(js) for _ in range(5)]
            print(f"  {name:13}: {statistics.median(xs):7.1f} ms  (median of 5)")
        b.close()


if __name__ == "__main__":
    main()
