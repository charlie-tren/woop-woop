"""Rasterise ASSEMBLED water polygons into the continental land-mask grid.

Why this exists, and why the obvious approach failed.

The build decides water by burning way outlines into a grid and filling the holes. That
works for a small lake mapped as one closed way and fails for a big multipolygon, and
the failure is silent. Sydney Harbour is the case that exposed it: it is a
natural=water / water=harbour RELATION, its rings are 41 untagged member ways, and OSM's
coastline crosses the harbour mouth rather than tracing the shoreline - so the ocean
flood cannot reach it (the harbour sits in the same connected region as the whole
inland continent) and the outline fill cannot close it. The harbour came out as
standable land, and the isochrone fill was painted over it.

check_water.py already had the right tool for this and used it only to sieve peaks:
osmium's own multipolygon ASSEMBLER, which builds real geometry instead of guessing from
a grid. This applies it to the mask.

    python build/water_areas.py [extract.osm.pbf]

Writes data/au/water500.npz, which merge ORs into the land mask as water.
"""
import sys, time
import numpy as np
import osmium
from PIL import Image, ImageDraw
sys.path.insert(0, "build")
from au import AUS
from raster import Grid

# STRICT only, matching check_water.py. Wetland is deliberately excluded: a claypan or
# a salt flat is walkable and Lake Eyre is dry most of the year, so calling it water
# would both shrink the map and contradict the build's own definition.
STRICT = {("natural", "water"), ("landuse", "reservoir"), ("waterway", "riverbank")}
LAND_CELL = 250.0

# A ring smaller than this is not drawn, and the reason is not tidiness.
#
# ImageDraw fills at least one pixel for any polygon, however small, so at 500 m every
# tagged farm dam, ornamental pond and backyard swimming pool became a quarter of a
# square kilometre of water. Sydney came out as a fat blob covering the CBD, because
# dense suburbs hold thousands of them. Measured: 24% of the Sydney box read as water.
#
# Half a cell, so anything that would genuinely dominate a cell still draws. Sydney
# Harbour is about 55 km2 and Lake Eyre 9,500; a pool is 50 m2.
MIN_RING_M2 = 0.5 * LAND_CELL * LAND_CELL


def ring_area_m2(pts):
    """Shoelace, on the already-projected pixel coordinates."""
    n = len(pts)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5 * LAND_CELL * LAND_CELL


def keep_substantial(water):
    """Drop water bodies that are nowhere wider than about two cells.

    The area threshold above stops a swimming pool becoming a quarter square kilometre,
    but it cannot stop a RIVER: the Todd through Alice Springs is 50 to 100 m wide and
    several kilometres long, so its area clears the threshold easily and at 500 m it
    painted a water stripe straight through the town. Every settlement on a mapped river
    had the same problem, which would have punched holes in the fill inland.

    Width is what separates the Todd from Sydney Harbour - 50 m against 1 to 3 km - and
    both are long and convoluted, so no area or compactness test tells them apart.

    So: label the bodies, erode by one cell, and keep any body with a surviving cell -
    WHOLE, not eroded. A body substantial somewhere is kept in full, which is why the
    harbour keeps its narrow arms while a uniformly thin river disappears entirely. A
    plain opening loses those arms; measured, it dropped Middle Harbour.
    """
    from scipy import ndimage
    eroded = ndimage.binary_erosion(water, iterations=1)
    lab, _ = ndimage.label(water)
    keep = np.isin(lab, np.unique(lab[eroded & (lab > 0)]))
    print(f"  kept {100*keep.mean():.2f}% of the box as water, from "
          f"{100*water.mean():.2f}% drawn - the difference is rivers and channels "
          f"narrower than a cell")
    return keep


def main(pbf="data/australia-latest.osm.pbf"):
    g = Grid(AUS, LAND_CELL)
    print(f"water grid {g.w} x {g.h} at {LAND_CELL:.0f} m")
    img = Image.new("1", (g.w, g.h), 0)
    d = ImageDraw.Draw(img)

    t0, n, drawn, errs, holes, small = time.time(), 0, 0, 0, 0, 0
    fp = osmium.FileProcessor(pbf).with_areas()
    for a in fp:
        if not isinstance(a, osmium.osm.Area):
            continue
        n += 1
        if n % 500_000 == 0:
            print(f"  {n/1e6:.1f}M areas, {drawn:,} water drawn "
                  f"({time.time()-t0:.0f}s)", flush=True)
        if not ({(k, v) for k, v in a.tags} & STRICT):
            continue
        try:
            for ring in a.outer_rings():
                pts = [g.to_px(nd.lon, nd.lat) for nd in ring]
                if len(pts) >= 3 and ring_area_m2(pts) >= MIN_RING_M2:
                    d.polygon(pts, fill=1)
                    drawn += 1
                elif len(pts) >= 3:
                    small += 1
                    continue
                # Islands inside a lake are land, so punch them back out. The outline
                # fill this replaces marked them as WATER, because it could not tell an
                # inner ring from an outer one.
                for inner in a.inner_rings(ring):
                    ipts = [g.to_px(nd.lon, nd.lat) for nd in inner]
                    if len(ipts) >= 3:
                        d.polygon(ipts, fill=0)
                        holes += 1
        except Exception:
            errs += 1

    water = np.array(img, dtype=bool)
    print(f"  {n:,} areas seen, {drawn:,} water rings drawn, {small:,} too small "
          f"to matter at {LAND_CELL:.0f} m, {holes:,} islands punched out, "
          f"{errs:,} failed")
    print(f"  {100*water.mean():.2f}% of the box is polygon water")
    np.savez_compressed("data/au/water500.npz", water=water,
                        bbox=np.array(AUS), cell=LAND_CELL)
    print(f"  -> data/au/water500.npz ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main(*sys.argv[1:])
