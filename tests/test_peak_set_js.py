"""Which peak file answers which mode, tested through the shipped JS.

There are now three peak sets and they are not interchangeable - each maximises a
different field over a different surface - so picking the wrong one is not a rendering
bug, it is a wrong answer with a confident caption. That has already happened once: Ride
answered from the drive-only file for weeks, so a bicycle could only be offered somewhere
a car could go, and the card said "from anything but roads" about it.

These lift `activeSet` and `walksToSpot` out of docs/app.js and run the real functions,
rather than restating the rule in Python where it could agree with itself while the
shipped code disagreed.

The case that matters most is the FALLBACK: an older deploy has no peaks-bike.bin, and
bike must then fall back to the drive file rather than to the walked set. Falling back to
the walked set would offer a bicycle the end of a footpath.
"""
import json
import os
import shutil
import subprocess
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "docs", "app.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")


def lift(src, name):
    """One function's source, by walking braces from its first one.

    Slicing to the next `}` at column zero would be shorter and would break on any
    brace inside a string or comment in between. Counting depth is the version that
    cannot be fooled by the file growing around it.
    """
    i = src.index("function " + name + "(")
    depth, j = 0, src.index("{", i)
    for j in range(j, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                break
    return src[i:j + 1]


def run(cases):
    """Run the lifted functions as a real module - no eval, no new Function."""
    src = open(APP, encoding="utf-8").read()
    body = lift(src, "activeSet") + "\n" + lift(src, "walksToSpot")
    driver = (
        body + "\n"
        "const cases = JSON.parse(process.argv[2]);\n"
        "const out = cases.map(c => {\n"
        "  state = { mode: c.mode };\n"
        "  P = 'P';\n"
        "  PD = c.hasDrive ? 'PD' : null;\n"
        "  PB = c.hasBike ? 'PB' : null;\n"
        "  return { set: activeSet(), walks: walksToSpot() };\n"
        "});\n"
        "console.log(JSON.stringify(out));\n"
    )
    # `var` so the lifted functions see them as the globals they expect.
    driver = "var state, P, PD, PB;\n" + driver
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "peakset.js")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(driver)
        proc = subprocess.run(["node", path, json.dumps(cases)],
                              capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


ALL_FILES = dict(hasBike=True, hasDrive=True)


def result(mode, **kw):
    return run([dict(mode=mode, **{**ALL_FILES, **kw})])[0]


def test_foot_always_uses_the_walked_set():
    assert result("foot")["set"] == "P"
    assert result("foot", hasBike=False, hasDrive=False)["set"] == "P"


def test_bike_uses_its_own_file_when_present():
    assert result("bike")["set"] == "PB"


def test_car_ignores_the_bike_file():
    """The bike surface includes ways a car cannot use, so this must not drift."""
    assert result("car")["set"] == "PD"


def test_bike_falls_back_to_drive_not_to_the_walked_set():
    r = result("bike", hasBike=False)
    assert r["set"] == "PD", "bike without its own file must not answer from the foot set"


def test_bike_with_no_vehicle_file_at_all_falls_back_to_walked():
    assert result("bike", hasBike=False, hasDrive=False)["set"] == "P"


def test_a_vehicle_reaches_the_spot_whenever_its_own_file_is_loaded():
    assert result("bike")["walks"] is True
    assert result("car")["walks"] is True


def test_bike_reaches_the_spot_on_its_own_file_without_the_drive_file():
    """The old condition was `!!PD`, which would say no here - a bike on a cycleway
    reaches its answer whether or not the drive file happens to be loaded."""
    assert result("bike", hasDrive=False)["walks"] is True


def test_no_vehicle_file_means_no_claim_that_the_vehicle_arrives():
    assert result("car", hasBike=False, hasDrive=False)["walks"] is False
