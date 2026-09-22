"""Mine a peak set a BICYCLE can reach, and pack it.

WHY A THIRD FILE. Ride and Drive both answer from the drive-only peaks, whose peaks sit on
a ROAD by construction. So a bicycle can currently only be offered somewhere a car could
go: every cycleway, rail trail and fire trail is unreachable by Ride, which is most of what
makes cycling interesting. The distance is measured to everything-except-roads too, so the
card reads "630 m from anything but roads" - a road-side number for a mode that need not be
on a road.

WHAT MAKES IT DIFFERENT FROM THE OTHER TWO, which is the only thing worth knowing:

    foot    surface = the ACCESS network, measured to ANYTHING
            (the track underfoot counts against the score, because you walked it)
    drive   surface = road, measured to BUILT
            (the road underfoot is excluded - you are on it, not near it)
    bike    surface = road OR way, measured to BUILT
            (same reasoning as drive, over a wider surface)

A KNOWN COARSENESS, stated because it changes what the number means. The chunk rasters
carry one "way" class covering cycleway, path, track, footway, bridleway and steps - they
were never split, and splitting them means re-extracting from the .pbf and rebuilding 79
chunks. So the bike surface includes a few things a bicycle cannot really use, notably
steps. For this product that trade is the right way round: a fire trail is the whole point,
and a peak misplaced onto a footpath is a worse answer than a peak on a fire trail but a
far better one than refusing to leave the road network at all.

    python build/bike_peaks.py mine     # per-chunk, resumable, skips finished chunks
    python build/bike_peaks.py pack     # -> docs/data/peaks-bike.bin

`mine` is a separate stage rather than a block inside au_build's chunks_stage because that
stage skips any chunk whose peak file exists, so adding to it would have forced a full
re-run of the foot, drive and land work to get one new file.
"""
import json
import os
import sys
import time

import numpy as np
from scipy import ndimage

sys.path.insert(0, "build")
from au import BUFFER, CELL, WORK, read_chunk
from au_build import BIKE_DIR, BIKE_SPACING_M, coarse_bits
from raster import BUILT, Grid, burn

BIKE_SURFACE = ("road", "way")


def one_chunk(i, box, coarse_ocean, coarse_comp, cg):
    from au_build import chunk_water

    d = read_chunk(f"{WORK}/c{i:03d}.bin")
    if d is None:
        return 0
    lon, lat, off, kind = d
    bbox = (box[0] - BUFFER, box[1] - BUFFER, box[2] + BUFFER, box[3] + BUFFER)
    g = Grid(bbox, CELL)

    surface = burn(g, lon, lat, off, np.isin(kind, list(BIKE_SURFACE)))
    built = burn(g, lon, lat, off, np.isin(kind, list(BUILT)))
    if not surface.any() or not built.any():
        return 0

    wet = chunk_water(g, lon, lat, off, kind, coarse_ocean, cg)

    # Only the chunk's OWN box is answerable; the buffer exists so the edges of that box
    # measure correctly and must never contribute peaks of its own.
    own = np.zeros_like(wet)
    x0, y0 = g.to_px(box[1], box[2])
    x1, y1 = g.to_px(box[3], box[0])
    own[max(0, int(np.ceil(y0))):int(y1), max(0, int(np.ceil(x0))):int(x1)] = True

    dist = ndimage.distance_transform_edt(~built, sampling=CELL).astype(np.float32)
    ok = own & (~wet) & surface
    if not ok.any():
        return 0

    k = int(round(BIKE_SPACING_M / CELL)) | 1
    field = np.where(ok, dist, -1)
    cand = ok & (field >= ndimage.maximum_filter(field, size=k, mode="constant", cval=-1))
    ys, xs = np.nonzero(cand)
    order = np.argsort(-dist[ys, xs])
    ys, xs = ys[order], xs[order]

    step = max(1, int(round(BIKE_SPACING_M / CELL)))
    taken = np.zeros((g.h // step + 2, g.w // step + 2), bool)
    rows = []
    for y, x in zip(ys, xs):
        gy, gx = y // step, x // step
        if taken[gy, gx]:
            continue
        taken[gy, gx] = True
        plon, plat = g.to_lonlat(x + 0.5, y + 0.5)
        ccx = int(np.clip((plon - cg.west) * cg.m_per_deg_lon / cg.cell, 0, cg.w - 1))
        ccy = int(np.clip((cg.north - plat) * cg.m_per_deg_lat / cg.cell, 0, cg.h - 1))
        rows.append((plat, plon, float(dist[y, x]), 0.0,
                     plat, plon, int(coarse_comp[ccy, ccx])))
    if rows:
        os.makedirs(BIKE_DIR, exist_ok=True)
        np.save(f"{BIKE_DIR}/q{i:03d}.npy", np.array(rows, dtype=np.float64))
    return len(rows)


def mine():
    cfg = json.load(open(f"{WORK}/chunks.json"))
    ocean, comp, cg = coarse_bits()
    total, t0 = 0, time.time()
    for i, box in enumerate(cfg["boxes"]):
        if os.path.exists(f"{BIKE_DIR}/q{i:03d}.npy"):
            continue
        n = one_chunk(i, box, ocean, comp, cg)
        total += n
        print(f"  chunk {i:3d} {n:6d} bike peaks  ({time.time() - t0:.0f}s)", flush=True)
    print(f"  {total:,} bike peaks mined")


if __name__ == "__main__":
    {"mine": mine}[sys.argv[1]]()
