"""The basemap-colour coastline, tested through the JS that actually ships.

The fill is the one piece of this app written in JavaScript that makes a real decision -
which pixels are sea - so testing a Python reimplementation of it would only prove that
two things I wrote agree. These tests lift `waterFromPixels` out of `docs/app.js` with
node and run the shipped code.

What it has to get right is not "is blue water", which is trivial, but the things drawn
ON TOP of water: ferry route dashes and place labels are not water-coloured, and without
the majority pass they punch thousands of land specks into the middle of the harbour.
"""
import json
import os
import shutil
import subprocess
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "docs", "app.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

RUNNER = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const start = src.indexOf('const WATER_RGB');
const end = src.indexOf('async function paintLandFillFromTiles');
if (start < 0 || end < 0 || end <= start) {
  console.error('SLICE_FAILED'); process.exit(2);
}
let slice = src.slice(start, end);
if (!/function waterFromPixels/.test(slice)) { console.error('NO_FN'); process.exit(2); }
slice += '\nmodule.exports = { waterFromPixels, WATER_RGB, WATER_TOL, MAJORITY_K };';
const mod = { exports: {} };
new Function('module', 'exports', slice)(mod, mod.exports);
const { waterFromPixels, WATER_RGB, WATER_TOL, MAJORITY_K } = mod.exports;

const spec = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const { w, h, land } = spec;                 // land: [x0,y0,x1,y1] boxes drawn on water
const px = new Uint8ClampedArray(w * h * 4);
for (let i = 0, j = 0; i < w * h; i++, j += 4) {
  px[j] = WATER_RGB[0]; px[j+1] = WATER_RGB[1]; px[j+2] = WATER_RGB[2]; px[j+3] = 255;
}
for (const [x0, y0, x1, y1] of land) {
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
    const j = (y * w + x) * 4;
    px[j] = 40; px[j+1] = 40; px[j+2] = 40;   // ink: nothing like the water blue
  }
}
const out = waterFromPixels(px, w, h);
let water = 0;
for (let i = 0; i < out.length; i++) water += out[i];
console.log(JSON.stringify({
  water: water, total: w * h, k: MAJORITY_K, tol: WATER_TOL,
  grid: Array.from({length: h}, (_, y) =>
    Array.from({length: w}, (_, x) => out[y * w + x]).join('')),
}));
"""


def run(tmp_path, w, h, land):
    runner = tmp_path / "r.js"
    runner.write_text(RUNNER, encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"w": w, "h": h, "land": land}), encoding="utf-8")
    p = subprocess.run(["node", str(runner), APP, str(spec)],
                       capture_output=True, text=True)
    assert p.returncode == 0, f"node failed: {p.stderr.strip()}"
    return json.loads(p.stdout)


def test_the_slice_is_actually_found(tmp_path):
    """A check that silently tests nothing looks exactly like a check that passes.

    If app.js is refactored so the slice markers move, every other test here would
    trivially pass on an empty function, so assert the extraction worked and that the
    constants came back with the values the tuning run chose.
    """
    r = run(tmp_path, 16, 16, [])
    assert r["k"] == 9, "the majority kernel changed; the 9x9 figure was measured"
    assert r["tol"] == 60
    assert r["water"] == r["total"], "an all-water image must come back all water"


def test_all_land_stays_land(tmp_path):
    r = run(tmp_path, 40, 40, [[0, 0, 40, 40]])
    assert r["water"] == 0


def test_a_ferry_dash_over_water_is_erased(tmp_path):
    """A 2 px line across open water is a route dash, not an isthmus."""
    dashes = [[x, 20, x + 6, 22] for x in range(0, 60, 10)]
    r = run(tmp_path, 60, 60, dashes)
    assert r["water"] == r["total"], (
        "dashes survived as land:\n" + "\n".join(r["grid"]))


def test_a_label_over_water_is_erased(tmp_path):
    """Scattered 3x5 blobs are lettering. Real land is not 15 px with gaps."""
    letters = [[x, 30, x + 3, 35] for x in range(5, 50, 5)]
    r = run(tmp_path, 60, 60, letters)
    assert r["water"] == r["total"], (
        "lettering survived as land:\n" + "\n".join(r["grid"]))


def test_a_real_island_survives(tmp_path):
    """The filter must not be so aggressive that it eats genuine small land.

    A 20x20 block at this zoom is about 100 m across, which is Shark Island, and it has
    to come through or the harbour islands vanish along with the labels.
    """
    r = run(tmp_path, 60, 60, [[20, 20, 40, 40]])
    kept = r["total"] - r["water"]
    assert kept > 200, f"the island was eaten, only {kept} px survived"


def test_dashes_beside_an_island_do_not_join_it(tmp_path):
    """Both behaviours at once, which is the real harbour case."""
    land = [[20, 20, 40, 40]] + [[x, 50, x + 6, 52] for x in range(0, 60, 10)]
    r = run(tmp_path, 60, 60, land)
    rows = r["grid"]
    assert all(c == "1" for c in rows[50]), "the dash row should be all water"
    assert rows[30][30] == "0", "the island centre should be land"
