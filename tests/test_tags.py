"""The tag logic, tested without installing the parser.

    python -m pytest tests -q

`want()` and `classify()` are pure functions over a tag mapping. The only thing
standing between them and a test is `import osmium` at the top of their modules, plus
shapely in check_water - heavy installs whose absence had left the two functions that
decide WHAT COUNTS AS AN OBSTACLE completely uncovered.

So the parsers are stubbed in sys.modules before the import. Nothing here calls into
osmium or shapely; they are imported and never touched on these paths.
"""
from __future__ import annotations

import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "build"))

# Stub before import. Only the module-level `import` needs satisfying.
for name in ("osmium", "shapely", "shapely.geometry", "shapely.strtree"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["osmium"].SimpleHandler = object
for attr in ("Point", "shape"):
    setattr(sys.modules["shapely.geometry"], attr, object)
sys.modules["shapely.strtree"].STRtree = object

from extract import want, CIVILISATION, ACCESS_ONLY      # noqa: E402
from check_water import classify, STRICT, MARSH          # noqa: E402


# ------------------------------------------------- civilisation vs access

@pytest.mark.parametrize("hw", sorted(CIVILISATION))
def test_a_road_is_something_to_get_away_from(hw):
    assert want({"highway": hw}) == "road"


@pytest.mark.parametrize("hw", sorted(ACCESS_ONLY))
def test_a_track_is_a_way_in_and_not_civilisation(hw):
    """The first version had `track` in the civilisation set, which made the answer the
    middle of a forest with no way in, because being far from every track was part of
    what it maximised. This is the regression that would silently return."""
    assert want({"highway": hw}) == "way"


def test_the_two_networks_do_not_overlap():
    assert not (CIVILISATION & ACCESS_ONLY), CIVILISATION & ACCESS_ONLY


# The two parametrised tests above walk the sets themselves, so they prove `want` agrees
# with them and NOTHING about what is in them: move "track" from one set to the other and
# both still pass, because the case simply moves with it. A probe caught exactly that.
# These pin the memberships the project's own reasoning turns on, as literals.
@pytest.mark.parametrize("hw,expected", [
    ("track", "way"),          # the documented original bug - a fire trail is the way IN
    ("path", "way"),
    ("footway", "way"),
    ("motorway", "road"),
    ("residential", "road"),
    ("service", "road"),       # a driveway means a house
])
def test_the_memberships_the_answer_depends_on(hw, expected):
    assert want({"highway": hw}) == expected


@pytest.mark.parametrize("tags,expected", [
    ({"building": "house"}, "building"),
    ({"building": "yes"}, "building"),
    ({"railway": "rail"}, "rail"),
    ({"railway": "tram"}, "rail"),
    ({"railway": "abandoned"}, None),        # a disused line is not civilisation
    ({"power": "line"}, "power"),
    ({"power": "pole"}, None),               # a pole is not a line
    ({"aeroway": "runway"}, "aero"),
    ({"aeroway": "gate"}, None),
    ({"natural": "coastline"}, "coast"),
    ({}, None),
    ({"name": "Somewhere"}, None),
])
def test_the_rest_of_the_families(tags, expected):
    assert want(tags) == expected


# ------------------------------------------------------------------ water

@pytest.mark.parametrize("tags", [
    {"natural": "water"}, {"natural": "wetland"},
    {"waterway": "riverbank"}, {"landuse": "reservoir"},
])
def test_water_is_captured_separately_from_anything(tags):
    """Water is not 'anything' - an empty lake is empty - but you cannot stand in it,
    so it has its own class rather than being ignored."""
    assert want(tags) == "water"


def test_a_river_line_is_not_water():
    # waterway=river is a centreline, not an area. Treating it as water would exclude
    # a strip of land either side of every creek in the country.
    assert want({"waterway": "river"}) is None


def test_the_water_checker_covers_everything_the_build_calls_water():
    """check_water.py exists to verify the build with a DIFFERENT implementation, so
    its definition of water has to cover the build's. Anything the build excludes that
    the checker does not know about is a blind spot in the only independent check, and
    it would look like a clean report."""
    build_water = set()
    for k, v in [("natural", "water"), ("natural", "wetland"),
                 ("waterway", "riverbank"), ("landuse", "reservoir")]:
        if want({k: v}) == "water":
            build_water.add((k, v))
    assert build_water <= (STRICT | MARSH), build_water - (STRICT | MARSH)


def test_strict_and_marsh_stay_separate():
    """Reporting them together would blame the build for a definition it never made:
    the build does not exclude wetland, and a claypan is walkable."""
    assert not (STRICT & MARSH)


# ---------------------------------------------------------------- classify
# classify() takes an ITERABLE OF PAIRS, not a mapping - it is handed osmium's tag list
# directly. Passing it a dict would silently iterate the keys and match nothing, so the
# shape is part of what these pin.

@pytest.mark.parametrize("tags,expected", [
    ([("natural", "water")], "strict"),
    ([("landuse", "reservoir")], "strict"),
    ([("waterway", "riverbank")], "strict"),
    ([("natural", "wetland")], "marsh"),
    ([("highway", "track")], None),
    ([], None),
    ([("name", "Lake Eyre")], None),           # a name is not a claim about water
    ([("natural", "water"), ("name", "X")], "strict"),
])
def test_classify_reads_pairs_and_splits_open_water_from_marsh(tags, expected):
    assert classify(tags) == expected


def test_strict_wins_over_marsh_when_both_are_present():
    """A wetland tagged as water too is open water: the stricter reading is the one that
    keeps someone out of a lake."""
    assert classify([("natural", "wetland"), ("landuse", "reservoir")]) == "strict"
