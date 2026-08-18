"""Stages two and three of the continent build: coarse components, then per-chunk peaks.

Run after build/au.py has bucketed the extract.

  python build/au_build.py coarse    # one 2 km land/ocean grid for the continent
  python build/au_build.py chunks    # rasterise each chunk, mine its peaks
  python build/au_build.py merge     # concatenate into docs/data/peaks.*
"""
import json, os, sys, time
import numpy as np
from scipy import ndimage
sys.path.insert(0, "build")
from au import WORK, AUS, BUFFER, CELL, COARSE, read_chunk
from raster import Grid, burn, ANYTHING, ACCESS

# ON the path network, not near it. A spot 140 m into scrub is not somewhere Google
# Maps can navigate you to, and "walk to the coordinates and then bush-bash" is not an
# answer. Requiring reach == 0 means every peak sits on a road, track or footpath, so
# the whole trip is navigable. Roads and buildings are still what the distance is
# measured TO, so the winners are points on fire trails and walking tracks a long way
# from anything built.
ON_NETWORK = True
SPACING_M = 1000
# No floor. The question is "the MOST empty place you can get to", not "an empty place":
# in a dense city the honest answer is a spot 80 m from a road, and a 100 m floor
# deleted those, which is why walking from a capital so often had no answer at all.
MIN_DIST_M = 0
DIST_SCALE_M = 10    # units the packed distances are stored in
PEAK_DIR = f"{WORK}/peaks"


# ------------------------------------------------------------------ stage: coarse
def coarse():
    """One land/ocean grid for the whole continent, at 2 km.

    Components must be GLOBAL. Labelling each chunk separately renumbers at every
    border, so the same island would be component 3 in one chunk and component 7 in the
    next, and "same landmass as me" would be meaningless across a seam.
    """
    cfg = json.load(open(f"{WORK}/chunks.json"))
    g = Grid(AUS, COARSE)
    print(f"coarse grid {g.w} x {g.h} at {COARSE:.0f} m")

    coast = np.zeros((g.h, g.w), bool)
    water = np.zeros((g.h, g.w), bool)
    for i, box in enumerate(cfg["boxes"]):
        d = read_chunk(f"{WORK}/c{i:03d}.bin")
        if d is None:
            continue
        lon, lat, off, kind = d
        if (kind == "coast").any():
            coast |= burn(g, lon, lat, off, kind == "coast")
        if (kind == "water").any():
            water |= burn(g, lon, lat, off, kind == "water")
        if i % 10 == 0:
            print(f"  chunk {i}/{len(cfg['boxes'])}", flush=True)

    barrier = coast.copy()
    barrier[0, :] = barrier[-1, :] = True
    barrier[:, 0] = barrier[:, -1] = True
    lab, _ = ndimage.label(~barrier)
    # Seed from the middle of the western edge - open Indian Ocean at every latitude
    # of this box, unlike the north and east which are full of islands.
    col = 1
    rows = np.where(lab[:, col] > 0)[0]
    ocean = lab == lab[rows[len(rows) // 2], col]
    if not 0.2 < ocean.mean() < 0.9:
        raise RuntimeError(f"ocean is {100*ocean.mean():.0f}% of the box - "
                           "the coastline barrier is leaking or sealed")
    lakes = ndimage.binary_fill_holes(water)
    wet = ocean | lakes
    wet[0, :] = wet[-1, :] = True
    wet[:, 0] = wet[:, -1] = True

    comp, n = ndimage.label(~wet)
    sizes = np.bincount(comp.ravel())
    print(f"  {100*ocean.mean():.0f}% ocean, {n} land components, "
          f"largest {sizes[1:].max() * (COARSE/1000)**2:,.0f} km2")
    # OCEAN is saved separately from WET. The fine pass seeds its flood from this, and
    # seeding from wet marks the whole continent as water: an inland lake is wet, the
    # fine labelling only splits on the COASTLINE, so a single lake cell lands on the
    # one enormous inland label and drags all of it under. Measured: 98.6% of the
    # Gympie chunk came back as water, and 660 peaks instead of ~30,000.
    # LAKES are saved too, filled at continent scale where nothing is clipped. A lake
    # that straddles a chunk boundary has an open outline in the chunk below it, so the
    # per-chunk fill cannot close it and the middle of the lake stays "land" - which is
    # how Lake Eyre ended up holding the best answers in South Australia.
    np.savez_compressed(f"{WORK}/coarse.npz", wet=wet, ocean=ocean, lakes=lakes,
                        comp=comp.astype(np.int32),
                        bbox=np.array(AUS), cell=COARSE)
    print(f"  -> {WORK}/coarse.npz")


# ------------------------------------------------------------------ stage: chunks
def one_chunk(i, box, coarse_ocean, coarse_comp, cg):
    d = read_chunk(f"{WORK}/c{i:03d}.bin")
    if d is None:
        return 0
    lon, lat, off, kind = d
    bbox = (box[0] - BUFFER, box[1] - BUFFER, box[2] + BUFFER, box[3] + BUFFER)
    g = Grid(bbox, CELL)

    occ = burn(g, lon, lat, off, np.isin(kind, list(ANYTHING)))
    if not occ.any():
        return 0
    acc = burn(g, lon, lat, off, np.isin(kind, list(ACCESS)))
    dist = ndimage.distance_transform_edt(~occ, sampling=CELL).astype(np.float32)
    reach = ndimage.distance_transform_edt(~acc, sampling=CELL).astype(np.float32)

    # Fine water mask, seeded from the coarse ocean rather than from a box edge. A
    # chunk edge is not reliably ocean, and a flood seeded on the wrong side of the
    # coast inverts the whole mask while still looking plausible.
    ys, xs = np.mgrid[0:g.h, 0:g.w]
    clon, clat = g.to_lonlat(xs + 0.5, ys + 0.5)
    cx = np.clip(((clon - cg.west) * cg.m_per_deg_lon / cg.cell).astype(int),
                 0, cg.w - 1)
    cy = np.clip(((cg.north - clat) * cg.m_per_deg_lat / cg.cell).astype(int),
                 0, cg.h - 1)
    # Eroded by two coarse cells (4 km) so every seed is unambiguously open water.
    # At 2 km the coarse ocean overlaps fine LAND near any coastline, and one bad seed
    # cell contaminates the entire connected label it lands on.
    seed = coarse_ocean[cy, cx]
    fine_coast = burn(g, lon, lat, off, kind == "coast")
    lab, _ = ndimage.label(~fine_coast)
    wet_labels = np.unique(lab[seed & (lab > 0)])
    wet = np.isin(lab, wet_labels)
    if (kind == "water").any():
        fine_water = burn(g, lon, lat, off, kind == "water")
        # Small lakes close on their own outline, so a plain fill handles them.
        wet |= ndimage.binary_fill_holes(fine_water)
        # Big lakes clipped by a chunk boundary are NOT handled here, and deliberately
        # so. Two rasters attempts both failed in opposite directions: label-and-seed
        # from the coarse mask flooded the continent (1.09M peaks -> 9), and using the
        # coarse fill directly marked all of Brisbane as water, because at 2 km the
        # river, the bay and the city's ponds form a ring and binary_fill_holes fills
        # everything inside it. Measured: 100% of a 12 km box over the CBD.
        #
        # The polygon sieve in build/check_water.py is the authority on water anyway -
        # it assembles real multipolygons instead of guessing from a grid - so the
        # raster only has to be roughly right and the sieve removes the rest.

    # Only the chunk's OWN box is answerable; the buffer exists so the edges of that
    # box measure correctly, and must never contribute peaks of its own.
    own = np.zeros_like(wet)
    x0, y0 = g.to_px(box[1], box[2])
    x1, y1 = g.to_px(box[3], box[0])
    own[max(0, int(np.ceil(y0))):int(y1), max(0, int(np.ceil(x0))):int(x1)] = True

    ok = own & (~wet) & (reach <= 0.0) & (dist >= MIN_DIST_M)
    if not ok.any():
        return 0

    k = int(round(SPACING_M / CELL)) | 1
    field = np.where(ok, dist, -1)
    cand = ok & (field >= ndimage.maximum_filter(field, size=k, mode="constant",
                                                 cval=-1))
    ys, xs = np.nonzero(cand)
    order = np.argsort(-dist[ys, xs])
    ys, xs = ys[order], xs[order]

    _, (ay, ax) = ndimage.distance_transform_edt(~occ, return_indices=True)
    step = max(1, int(round(SPACING_M / CELL)))
    taken = np.zeros((g.h // step + 2, g.w // step + 2), bool)
    rows = []
    for y, x in zip(ys, xs):
        gy, gx = y // step, x // step
        if taken[gy, gx]:
            continue
        taken[gy, gx] = True
        plon, plat = g.to_lonlat(x + 0.5, y + 0.5)
        alon, alat = g.to_lonlat(int(ax[y, x]) + 0.5, int(ay[y, x]) + 0.5)
        ccx = int(np.clip((plon - cg.west) * cg.m_per_deg_lon / cg.cell, 0, cg.w - 1))
        ccy = int(np.clip((cg.north - plat) * cg.m_per_deg_lat / cg.cell, 0, cg.h - 1))
        rows.append((plat, plon, float(dist[y, x]), float(reach[y, x]),
                     alat, alon, int(coarse_comp[ccy, ccx])))
    if not rows:
        return 0
    os.makedirs(PEAK_DIR, exist_ok=True)
    np.save(f"{PEAK_DIR}/p{i:03d}.npy", np.array(rows, dtype=np.float64))
    return len(rows)


def chunks_stage():
    cfg = json.load(open(f"{WORK}/chunks.json"))
    c = np.load(f"{WORK}/coarse.npz")
    cg = Grid(tuple(c["bbox"]), float(c["cell"]))
    ocean = ndimage.binary_erosion(c["ocean"], iterations=2)
    comp = c["comp"]
    total, t0 = 0, time.time()
    for i, box in enumerate(cfg["boxes"]):
        if os.path.exists(f"{PEAK_DIR}/p{i:03d}.npy"):
            continue
        n = one_chunk(i, box, ocean, comp, cg)
        total += n
        print(f"  chunk {i:3d} {str(box):32} {n:6d} peaks  "
              f"({time.time()-t0:.0f}s)", flush=True)
    print(f"  {total:,} peaks across all chunks")


# ------------------------------------------------------------------- stage: merge
def merge(out="docs/data/peaks.json"):
    """Concatenate the chunk peak lists, prune, and pack for the browser."""
    cfg = json.load(open(f"{WORK}/chunks.json"))
    c = np.load(f"{WORK}/coarse.npz")
    rows = []
    for i in range(len(cfg["boxes"])):
        p = f"{PEAK_DIR}/p{i:03d}.npy"
        if os.path.exists(p):
            rows.append(np.load(p))
    a = np.concatenate(rows)
    print(f"  {len(a):,} raw peaks")

    # PRUNE. 93% of peaks are under 500 m from something - a paddock between two roads,
    # not a remote place, and 20 MB of them. But dropping them outright leaves someone
    # walking for an hour in a city with no answer at all, which reads as broken. So
    # they are kept at a much coarser spacing: every genuinely remote peak survives,
    # and cities keep one candidate every 5 km instead of one every kilometre.
    # 2 km, not 5. At 5 km the small peaks were so sparse that an hour's WALK - a
    # 3.6 km radius - usually contained none of them at all, and the page simply said
    # nothing was in range. Measured: 84% of walking queries from the five biggest
    # cities returned no answer. 2 km costs about 2.5 MB more over the wire and makes
    # the mode work.
    KEEP_ALL_ABOVE, COARSE_SPACING_DEG = 500.0, 2.0 / 111.0
    strong = a[a[:, 2] >= KEEP_ALL_ABOVE]
    weak = a[a[:, 2] < KEEP_ALL_ABOVE]
    weak = weak[np.argsort(-weak[:, 2])]
    key = (np.floor(weak[:, 0] / COARSE_SPACING_DEG).astype(np.int64) * 1_000_000
           + np.floor(weak[:, 1] / COARSE_SPACING_DEG).astype(np.int64))
    _, first = np.unique(key, return_index=True)
    a = np.concatenate([strong, weak[np.sort(first)]])
    a = a[np.argsort(-a[:, 2])]
    print(f"  {len(strong):,} at or above {KEEP_ALL_ABOVE:.0f} m, "
          f"{len(first):,} thinned below it -> {len(a):,} shipped")
    print(f"  best {a[0,2]/1000:.1f} km at {a[0,0]:.4f}, {a[0,1]:.4f}")

    # Components are computed from the OCEAN alone, not from wet.
    #
    # The question a component answers is "can I get there without a boat", and that is
    # about the SEA. Including lakes and rivers made every capital its own landmass: at
    # 2 km a tagged river is a two-kilometre barrier, so Sydney, Melbourne and Perth
    # were each severed from the interior and answered only from their own scrap of
    # coast - Sydney returned 0.7 km whether you had one hour or four. Roads bridge
    # rivers; the sea is the only thing that actually stops you.
    comp, _ = ndimage.label(~c["ocean"])
    print(f"  {comp.max()} landmasses by sea alone")

    # Peaks stored the id from the old grid, so re-read each one from the new one.
    cg = Grid(tuple(c["bbox"]), float(c["cell"]))
    px = np.clip(((a[:, 1] - cg.west) * cg.m_per_deg_lon / cg.cell).astype(int),
                 0, cg.w - 1)
    py = np.clip(((cg.north - a[:, 0]) * cg.m_per_deg_lat / cg.cell).astype(int),
                 0, cg.h - 1)
    a[:, 6] = comp[py, px]
    sizes = np.bincount(comp.ravel())
    order = np.argsort(-sizes[1:]) + 1
    remap = np.zeros(sizes.size, dtype=np.uint8)
    for rank, old_id in enumerate(order[:254], start=1):
        remap[old_id] = rank
    small = remap[comp][::2, ::2]     # 4 km is ample to answer "which landmass"
    open(out.replace(".json", "-comp.bin"), "wb").write(small.tobytes())

    n = len(a)
    blob = b"".join([
        np.rint(a[:, 0] * 1e5).astype("<i4").tobytes(),
        np.rint(a[:, 1] * 1e5).astype("<i4").tobytes(),
        # DECAMETRES, not metres. Australia's best peak is 176.7 km from anything and
        # a uint16 of metres tops out at 65.5 km, so metres silently clipped the entire
        # desert to the same number. Ten-metre precision is far finer than a distance
        # measured on a 100 m grid.
        np.rint(np.clip(a[:, 2] / DIST_SCALE_M, 0, 65535)).astype("<u2").tobytes(),
        np.rint(np.clip(a[:, 3] / DIST_SCALE_M, 0, 65535)).astype("<u2").tobytes(),
        np.rint(a[:, 4] * 1e5).astype("<i4").tobytes(),
        np.rint(a[:, 5] * 1e5).astype("<i4").tobytes(),
        remap[np.clip(a[:, 6], 0, sizes.size - 1).astype(np.int64)].astype("<u2")
            .tobytes(),
    ])
    open(out.replace(".json", ".bin"), "wb").write(blob)

    s_, w_, n_, e_ = [float(v) for v in c["bbox"]]
    json.dump({
        "count": n, "coord_scale": 1e5, "dist_scale_m": DIST_SCALE_M,
        "on_network": ON_NETWORK, "spacing_m": SPACING_M,
        "fields": ["lat:i4", "lon:i4", "d:u2", "off:u2", "alat:i4", "alon:i4", "c:u2"],
        "comp": {"width": int(small.shape[1]), "height": int(small.shape[0]),
                 "south": s_, "west": w_, "north": n_, "east": e_,
                 "bytes": 1},
        "max_m": float(a[0, 2]), "area": "Australia",
    }, open(out, "w"), indent=1)
    for f in (out, out.replace(".json", ".bin"), out.replace(".json", "-comp.bin")):
        print(f"  -> {f}  {os.path.getsize(f)/1024:,.0f} KB")


if __name__ == "__main__":
    {"coarse": coarse, "chunks": chunks_stage, "merge": merge}[sys.argv[1]]()
