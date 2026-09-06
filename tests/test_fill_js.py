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
slice += '\nmodule.exports = { waterFromPixels, WATER_RGB, WATER_CH_TOL, MAJORITY_K };';
const mod = { exports: {} };
new Function('module', 'exports', slice)(mod, mod.exports);
const { waterFromPixels, WATER_RGB, WATER_CH_TOL, MAJORITY_K } = mod.exports;

const spec = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const { w, h, land } = spec;                 // land: [x0,y0,x1,y1] boxes drawn on water
const bg = spec.bg || WATER_RGB;
const px = new Uint8ClampedArray(w * h * 4);
for (let i = 0, j = 0; i < w * h; i++, j += 4) {
  px[j] = bg[0]; px[j+1] = bg[1]; px[j+2] = bg[2]; px[j+3] = 255;
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
  water: water, total: w * h, k: MAJORITY_K, tol: WATER_CH_TOL,
  grid: Array.from({length: h}, (_, y) =>
    Array.from({length: w}, (_, x) => out[y * w + x]).join('')),
}));
"""


def run(tmp_path, w, h, land, bg=None):
    runner = tmp_path / "r.js"
    runner.write_text(RUNNER, encoding="utf-8")
    spec = tmp_path / "spec.json"
    body = {"w": w, "h": h, "land": land}
    if bg is not None:
        body["bg"] = list(bg)
    spec.write_text(json.dumps(body), encoding="utf-8")
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
    assert r["tol"] == 20, "the per-channel tolerance changed"
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


# --------------------------------------------------------------------------------
# The colours that were actually getting through, from the tiles Charlie was looking
# at on 06/09/2026. Every one of these is a real OSM carto fill, and every one of them
# was classified as water by the old sum-of-channels test at tolerance 60 - which is
# why Rookwood Cemetery and the airport apron came out unfilled.

CARTO = {
    "water":            (170, 211, 223),   # #aad3df, the thing we do want
    "water antialias":  (177, 201, 211),
    "pale water":       (178, 219, 218),
    "halo water":       (196, 220, 231),   # water under a label's white halo
    "halo water 2":     (219, 236, 241),
    "halo water 3":     (213, 232, 234),
    "cemetery":         (170, 203, 175),   # #aacbaf - old sum distance 56
    "airport apron":    (187, 187, 204),   # old sum distance 60, exactly on it
    "grey building":    (212, 211, 211),   # old sum distance 54
    "residential":      (224, 223, 223),
    "park grass":       (205, 235, 176),
    "forest":           (173, 209, 158),
    "white label":      (255, 255, 255),   # the halo itself, not the water under it
}


@pytest.mark.parametrize("name", ["water", "water antialias", "pale water",
                                  "halo water", "halo water 2", "halo water 3"])
def test_water_shades_are_water(tmp_path, name):
    r = run(tmp_path, 32, 32, [], bg=CARTO[name])
    assert r["water"] == r["total"], f"{name} should classify as water"


@pytest.mark.parametrize("name", ["cemetery", "airport apron", "grey building",
                                  "residential", "park grass", "forest",
                                  "white label"])
def test_land_uses_are_not_water(tmp_path, name):
    """The regression that produced an unfilled Rookwood Cemetery.

    A whole tile of one land-use colour must come back with NO water in it at all.
    """
    r = run(tmp_path, 32, 32, [], bg=CARTO[name])
    assert r["water"] == 0, (
        f"{name} rgb{CARTO[name]} classified as water: "
        f"{r['water']} of {r['total']} px")


def test_cemetery_differs_from_water_almost_only_in_blue(tmp_path):
    """Why the old test failed, asserted so the reasoning cannot rot.

    Cemetery #aacbaf has the SAME red as water and green within 8, so a metric that adds
    the three channels together buries a 48-point miss in blue. Any replacement must stay
    per-channel, or Rookwood comes back.
    """
    water, cem = CARTO["water"], CARTO["cemetery"]
    assert water[0] == cem[0]
    assert abs(water[1] - cem[1]) <= 8
    assert abs(water[2] - cem[2]) >= 40
    assert sum(abs(a - b) for a, b in zip(water, cem)) <= 60, (
        "if this ever exceeds 60 the old test would have passed and this "
        "test no longer describes the bug it was written for")


def test_pure_white_is_not_water_however_pale_the_water_rule_gets(tmp_path):
    """The ceiling that makes the lightened-water rule safe.

    Accepting water blended towards white is only sound while it stops short of white
    itself: the map is full of white - label halos, paper background, building fill - and
    a rule that reaches all the way would flood the fill with holes wherever there is
    text. Water at f=1.0 IS white, so the ceiling sits below it.
    """
    r = run(tmp_path, 32, 32, [], bg=(255, 255, 255))
    assert r["water"] == 0
    # and one step back from white is still not water
    r2 = run(tmp_path, 32, 32, [], bg=(250, 250, 251))
    assert r2["water"] == 0
