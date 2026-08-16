"""Click each control and report what actually changed. A UI that silently no-ops
looks identical to one that works, so this asserts on the rendered text."""
import sys
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731/"


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        errs = []
        pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_timeout(2000)

        for mode in ("foot", "bike", "car", "foot"):
            pg.click(f'.modes button[data-mode="{mode}"]')
            pg.wait_for_timeout(900)
            first = pg.inner_text("#answer").split("\n")[0]
            pressed = pg.get_attribute(f'.modes button[data-mode="{mode}"]',
                                       "aria-pressed")
            print(f"  {mode:5} pressed={pressed:5}  -> {first}")

        print("\n  console errors:", errs or "(none)")
        b.close()


if __name__ == "__main__":
    main()
