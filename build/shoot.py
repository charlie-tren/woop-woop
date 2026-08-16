"""Load the page in a real browser, capture console errors, and screenshot it.

Console errors are collected and printed because a page that throws still renders a
map, and a screenshot alone would look like a working product.
"""
import sys
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731/"
OUT = sys.argv[2] if len(sys.argv) > 2 else "data/shot.png"


def main():
    errs, logs = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        pg.on("console", lambda m: (errs if m.type == "error" else logs).append(m.text))
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_timeout(2500)

        answer = pg.inner_text("#answer")
        print("--- answer panel ---")
        print(answer)
        print("--- console errors ---")
        print("\n".join(errs) if errs else "(none)")
        pg.screenshot(path=OUT)
        print("->", OUT)
        b.close()
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
