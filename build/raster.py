"""Turn extracted features into a distance-to-nearest-anything raster.

The measure is a straight Euclidean distance transform over a grid, not a per-point
nearest-neighbour search: you cannot evaluate every point against every feature, and
the distance transform gives the whole surface in one pass.

Grid is equirectangular with longitude scaled by cos(mean latitude), so a cell is
close enough to square in metres over a region this size (SEQ spans ~3 degrees; the
scale error across it is well under a percent, far below the resolution).
"""
import sys, time, numpy as np
from scipy import ndimage

R_EARTH = 6_371_000.0


class Grid:
    def __init__(self, bbox, cell_m):
        self.south, self.west, self.north, self.east = bbox
        self.cell = cell_m
        self.lat0 = (self.south + self.north) / 2
        self.kx = np.cos(np.radians(self.lat0))
        self.m_per_deg_lat = np.pi * R_EARTH / 180.0
        self.m_per_deg_lon = self.m_per_deg_lat * self.kx
        self.h = int(np.ceil((self.north - self.south) * self.m_per_deg_lat / cell_m))
        self.w = int(np.ceil((self.east - self.west) * self.m_per_deg_lon / cell_m))

    def to_px(self, lon, lat):
        x = (lon - self.west) * self.m_per_deg_lon / self.cell
        y = (self.north - lat) * self.m_per_deg_lat / self.cell   # row 0 = north
        return x, y

    def to_lonlat(self, x, y):
        lon = self.west + x * self.cell / self.m_per_deg_lon
        lat = self.north - y * self.cell / self.m_per_deg_lat
        return lon, lat


def burn(grid, lon, lat, off, want=None):
    """Mark every cell a feature passes through.

    Segments are densified to half-cell steps rather than run through a line
    algorithm - it is a few lines of numpy instead of a Python loop over millions of
    segments, and a missed cell here would read as emptiness that is not there.
    """
    x, y = grid.to_px(lon.astype(np.float64), lat.astype(np.float64))
    occ = np.zeros((grid.h, grid.w), dtype=bool)

    # Segment endpoints, dropping the joins BETWEEN features (off marks each break).
    keep = np.ones(len(x) - 1, dtype=bool)
    keep[off[1:-1] - 1] = False
    if want is not None:
        # Restrict to the chosen feature families by killing every segment that does
        # not belong to one - done on the segment mask so the geometry arrays are
        # never copied.
        seg_owner = np.zeros(len(x) - 1, dtype=np.int64)
        seg_owner[off[1:-1] - 1] = 1
        seg_owner = np.cumsum(seg_owner)
        keep &= want[seg_owner]
    x0, y0 = x[:-1][keep], y[:-1][keep]
    x1, y1 = x[1:][keep], y[1:][keep]

    steps = np.maximum(1, np.ceil(np.maximum(np.abs(x1 - x0), np.abs(y1 - y0)) * 2
                                  ).astype(np.int64))
    total = int(steps.sum()) + len(steps)
    print(f"  {len(steps):,} segments -> {total:,} sample points", flush=True)

    # Fractional position along each segment for every sample, built without a loop.
    idx = np.repeat(np.arange(len(steps)), steps + 1)
    starts = np.concatenate(([0], np.cumsum(steps + 1)[:-1]))
    t = (np.arange(total) - starts[idx]) / steps[idx]

    px = np.rint(x0[idx] + (x1[idx] - x0[idx]) * t).astype(np.int64)
    py = np.rint(y0[idx] + (y1[idx] - y0[idx]) * t).astype(np.int64)
    good = (px >= 0) & (px < grid.w) & (py >= 0) & (py < grid.h)
    occ[py[good], px[good]] = True
    return occ


# What counts as civilisation, and therefore what the distance is measured TO.
ANYTHING = {"road", "building", "rail", "power", "aero"}


def water_mask(grid, d):
    """Cells you cannot stand in: the ocean, plus lakes and reservoirs.

    The ocean is found by flooding inland from a seed in open water and letting the
    coastline stop it. Two details make that work rather than fail silently:

    * **The box border is part of the barrier.** Coastline ways are open lines that run
      off the edge of any extract, so the barrier is complete everywhere EXCEPT where
      the coast exits the box - here, the last two rows at the southern edge. Without a
      sealed border the flood escapes through that gap, wraps around the outside and
      marks the entire region as ocean (measured: 98.6% on the first attempt).
    * **The barrier is dilated by one cell**, closing sub-cell gaps where a coastline
      way is split between two OSM ways that do not share a vertex.

    Islands end up as land, which is right for the distance measure but means an island
    can win. That is acceptable only because reachability filters it later - see the
    German prior art, whose headline answer was a road-free island.
    """
    kind = d["kind"]
    coast = burn(grid, d["lon"], d["lat"], d["off"], kind == "coast")
    barrier = ndimage.binary_dilation(coast)
    barrier[0, :] = barrier[-1, :] = True
    barrier[:, 0] = barrier[:, -1] = True

    lab, _ = ndimage.label(~barrier)
    # Seed in open water: scan up the column just inside the eastern edge and take the
    # first free cell. Asserted rather than assumed - a seed that landed on the wrong
    # side of the coast would invert the whole mask and still look plausible.
    col = grid.w - 2
    rows = np.where(lab[:, col] > 0)[0]
    if len(rows) == 0:
        raise RuntimeError("no free cell on the eastern edge to seed the ocean from")
    ocean = lab == lab[rows[len(rows) // 2], col]
    if ocean.mean() > 0.9:
        raise RuntimeError(f"ocean mask is {100*ocean.mean():.0f}% of the box - "
                           "the coastline barrier is leaking")

    water = burn(grid, d["lon"], d["lat"], d["off"], kind == "water")
    lakes = ndimage.binary_fill_holes(water)

    # The outer ring is barrier, so it never joined the ocean component and would read
    # as empty land at the box edge - which is exactly where the first run put its
    # answer, 50 km out in the Coral Sea.
    wet = ocean | lakes
    wet[0, :] = wet[-1, :] = True
    wet[:, 0] = wet[:, -1] = True
    print(f"  water mask: {100*ocean.mean():.1f}% ocean, "
          f"{100*lakes.mean():.1f}% inland water")
    return wet


def run(features, cell_m, out):
    d = np.load(features, allow_pickle=True)
    bbox = tuple(d["bbox"])
    grid = Grid(bbox, cell_m)
    print(f"grid {grid.w} x {grid.h} at {cell_m} m  ({grid.w*grid.h/1e6:.1f}M cells)")

    serve = tuple(d["serve"]) if "serve" in d else bbox
    t0 = time.time()
    occ = burn(grid, d["lon"], d["lat"], d["off"], np.isin(d["kind"], list(ANYTHING)))
    print(f"  burned {occ.sum():,} occupied cells in {time.time()-t0:.0f}s")
    wet = water_mask(grid, d)

    t0 = time.time()
    dist = ndimage.distance_transform_edt(~occ, sampling=cell_m).astype(np.float32)
    print(f"  distance transform in {time.time()-t0:.0f}s")

    # Outside the serve box the distances are buffer-quality only: they exist so the
    # inner box measures correctly, and must never be returned as answers.
    answerable = np.zeros_like(wet)
    x0, y0 = grid.to_px(serve[1], serve[2])
    x1, y1 = grid.to_px(serve[3], serve[0])
    answerable[int(np.ceil(y0)):int(y1), int(np.ceil(x0)):int(x1)] = True
    print(f"  serve box is {100*answerable.mean():.0f}% of the buffered grid")

    np.savez_compressed(out, dist=dist, wet=wet, occ=occ, answerable=answerable,
                        bbox=np.array(bbox), serve=np.array(serve), cell=cell_m)
    print(f"  -> {out}")

    # Sanity checks that cost nothing and catch a flipped axis or a broken land mask.
    for label, field in (("ignoring water", dist),
                         ("on land", np.where(wet, -1, dist)),
                         ("on land, in box", np.where(wet | ~answerable, -1, dist))):
        iy, ix = np.unravel_index(np.argmax(field), field.shape)
        lon, lat = grid.to_lonlat(ix, iy)
        print(f"  furthest {label:15} {lat:.5f}, {lon:.5f}  "
              f"({field[iy,ix]/1000:.2f} km)")
    return grid, dist, wet, occ


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "data/seq-features.npz",
        float(sys.argv[2]) if len(sys.argv) > 2 else 100.0,
        sys.argv[3] if len(sys.argv) > 3 else "data/seq-dist.npz")
