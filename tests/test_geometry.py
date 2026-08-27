"""First tests for the answer-producing logic.

    python -m pytest tests -q

Woop Woop picks the emptiest point you can reach, and a wrong point looks exactly like
a right one - there is no error, no crash, and nobody can check it by eye. That is the
bar for needing tests.

SCOPED TO THE numpy/scipy FUNCTIONS ON PURPOSE. `extract.py` and `check_water.py` pull
in osmium and shapely, which makes a CI install a different order of job; `signs.py`
wants playwright and PIL. The functions here need neither, and they are the ones that
decide the answer rather than prepare the data for it.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "build"))

from fit_estimate import in_ring                    # noqa: E402
from query import solve                             # noqa: E402
from regions import REGIONS                         # noqa: E402


# ---------------------------------------------------------------- in_ring
# Ring points are (lon, lat) - x then y - while the function takes (lat, lon).
# Getting that pair the wrong way round is the single easiest mistake here, so the
# square below is deliberately NOT symmetric: it spans lon 0..4 and lat 0..2, so a
# swapped call lands outside and the test fails rather than passing by luck.
BOX = [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)]


@pytest.mark.parametrize("lat,lon,expected", [
    (1.0, 2.0, True),      # middle
    (1.0, 5.0, False),     # east of it
    (3.0, 2.0, False),     # north of it
    (1.0, -1.0, False),    # west of it
    (-1.0, 2.0, False),    # south of it
])
def test_in_ring_on_a_rectangle(lat, lon, expected):
    assert in_ring(lat, lon, BOX) is expected


def test_in_ring_excludes_a_concave_notch():
    """An isochrone follows a road network, so it is deeply concave - a point can sit
    inside the bounding box, between two arms, and be unreachable. Ray casting is what
    gets this right and a bounding-box test is what gets it wrong, so the notch is the
    case worth pinning."""
    #  an L: the notch is the top-right quadrant
    L = [(0.0, 0.0), (4.0, 0.0), (4.0, 1.0), (2.0, 1.0), (2.0, 4.0), (0.0, 4.0)]
    assert in_ring(0.5, 1.0, L) is True      # in the foot
    assert in_ring(3.0, 1.0, L) is True      # in the upright
    assert in_ring(3.0, 3.0, L) is False     # in the notch, inside the bbox
    assert in_ring(0.5, 3.5, L) is True      # far end of the foot


# ------------------------------------------------------------------ solve

class FakeGrid:
    """to_lonlat is all solve() asks of a grid. One cell, one degree."""
    def to_lonlat(self, ix, iy):
        return float(ix), float(iy)


def field(rows):
    return np.array(rows, dtype=float)


def test_solve_returns_the_furthest_first():
    d = field([[1, 5, 2], [9, 3, 4]])
    out = solve(d, FakeGrid(), top_n=3)
    assert [r["dist_m"] for r in out] == [9.0, 5.0, 4.0]


def test_solve_will_not_name_an_unreachable_point():
    """The naive maximum over a region is routinely a road-free island. Reachability is
    a hard filter, not a display option - the module docstring says so, and this is the
    assertion that keeps it true."""
    d = field([[1, 5, 2], [9, 3, 4]])
    reachable = np.array([[True, True, True], [False, True, True]])
    out = solve(d, FakeGrid(), reachable=reachable, top_n=3)
    assert 9.0 not in [r["dist_m"] for r in out]
    assert out[0]["dist_m"] == 5.0


def test_solve_will_not_name_a_point_in_water():
    """A shaded region that confidently covers water is the defect that gets noticed,
    because the map shows it."""
    d = field([[1, 5, 2], [9, 3, 4]])
    water = np.array([[False, True, False], [True, False, False]])
    out = solve(d, FakeGrid(), exclude_water=water, top_n=3)
    assert [r["dist_m"] for r in out] == [4.0, 3.0, 2.0]


def test_solve_returns_fewer_rather_than_masked_cells():
    """Masked cells are set to -1, not removed, so a top_n larger than the number of
    valid cells must drop them rather than report a -1 metre answer."""
    d = field([[1.0, 2.0], [3.0, 4.0]])
    reachable = np.array([[False, False], [False, True]])
    out = solve(d, FakeGrid(), reachable=reachable, top_n=4)
    assert len(out) == 1 and out[0]["dist_m"] == 4.0
    assert all(r["dist_m"] >= 0 for r in out)


def test_solve_reports_the_true_distance_not_the_masked_field():
    """field is a copy that gets -1s written into it; the reported dist_m must come
    from the original, or a masked neighbour would corrupt the number shown."""
    d = field([[7.0, 8.0]])
    out = solve(d, FakeGrid(), exclude_water=np.array([[False, True]]), top_n=1)
    assert out[0]["dist_m"] == 7.0


# ---------------------------------------------------------------- regions

@pytest.mark.parametrize("name", sorted(REGIONS))
def test_region_box_is_the_right_way_up(name):
    (south, west, north, east), slug = REGIONS[name]
    assert south < north, f"{name}: south {south} is not below north {north}"
    assert west < east, f"{name}: west {west} is not left of east {east}"
    assert -90 <= south and north <= 90, f"{name}: latitude out of range"
    assert -180 <= west and east <= 180, f"{name}: longitude out of range"
    assert slug, f"{name}: no Geofabrik extract slug"
