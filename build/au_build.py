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
from raster import Grid, burn, ANYTHING, ACCESS, BUILT

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
DRIVE_DIR = f"{WORK}/drive"
LAND_DIR = f"{WORK}/land"

# Drive-only peaks sit on the ROAD network, which is far denser than the set of remote
# track-ends the main field finds - Australia carries roughly 900,000 km of road, so a
# kilometre of suppression would mine close to a million of them. 2 km halves that and
# the merge prunes harder still.
DRIVE_SPACING_M = 2000

# The continental land mask shipped for clipping the drawn isochrone. 1 km, because
# Sydney Harbour is one to three kilometres wide and the 4 km component grid already
# shipped cannot resolve it at all - masking with that would eat real land.
LAND_CELL = 1000.0


def land_patch(g, wet, box, gg):
    """The chunk's own box, resampled from its 100 m water mask onto the 1 km grid.

    Sampled 3x3 within each coarse cell and called land only if every sample is land,
    so the reduction loses NARROW LAND rather than narrow water. That is the safe
    direction for the job: the mask exists to stop a fill being painted over a harbour,
    and leaving a sliver of foreshore unshaded is a far smaller lie than shading the
    harbour.
    """
    x0, y0 = gg.to_px(box[1], box[2])
    x1, y1 = gg.to_px(box[3], box[0])
    gx0, gy0 = max(0, int(np.floor(x0))), max(0, int(np.floor(y0)))
    gx1, gy1 = min(gg.w, int(np.ceil(x1))), min(gg.h, int(np.ceil(y1)))
    if gx1 <= gx0 or gy1 <= gy0:
        return None
    ys, xs = np.mgrid[gy0:gy1, gx0:gx1]
    land = np.ones(xs.shape, bool)
    for fy in (0.17, 0.5, 0.83):
        for fx in (0.17, 0.5, 0.83):
            lon, lat = gg.to_lonlat(xs + fx, ys + fy)
            cx, cy = g.to_px(lon, lat)
            cx = np.clip(cx.astype(np.int64), 0, g.w - 1)
            cy = np.clip(cy.astype(np.int64), 0, g.h - 1)
            land &= ~wet[cy, cx]
    return gx0, gy0, land


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
def one_chunk(i, box, coarse_ocean, coarse_comp, cg, gg):
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

    # ---- the continental land mask, for clipping the drawn isochrone ----
    lp = land_patch(g, wet, box, gg)
    if lp is not None:
        os.makedirs(LAND_DIR, exist_ok=True)
        np.savez_compressed(f"{LAND_DIR}/l{i:03d}.npz",
                            x0=lp[0], y0=lp[1], land=lp[2])

    # ---- drive-only peaks: the emptiest place you can PARK ----
    #
    # A different field, not a filter on the same one. The road underfoot is excluded
    # from the measurement (BUILT drops "road"), so the number means "how far from the
    # nearest house, railway, power line or runway", and the answer is somewhere you can
    # actually stop a car. The access point IS the spot, so the walked leg is zero.
    road = burn(g, lon, lat, off, kind == "road")
    built = burn(g, lon, lat, off, np.isin(kind, list(BUILT)))
    if road.any() and built.any():
        dpark = ndimage.distance_transform_edt(~built,
                                               sampling=CELL).astype(np.float32)
        okd = own & (~wet) & road
        if okd.any():
            kd = int(round(DRIVE_SPACING_M / CELL)) | 1
            fd = np.where(okd, dpark, -1)
            candd = okd & (fd >= ndimage.maximum_filter(fd, size=kd,
                                                        mode="constant", cval=-1))
            yd, xd = np.nonzero(candd)
            yd, xd = (lambda o: (yd[o], xd[o]))(np.argsort(-dpark[yd, xd]))
            stepd = max(1, int(round(DRIVE_SPACING_M / CELL)))
            takend = np.zeros((g.h // stepd + 2, g.w // stepd + 2), bool)
            drows = []
            for y, x in zip(yd, xd):
                gy, gx = y // stepd, x // stepd
                if takend[gy, gx]:
                    continue
                takend[gy, gx] = True
                plon, plat = g.to_lonlat(x + 0.5, y + 0.5)
                ccx = int(np.clip((plon - cg.west) * cg.m_per_deg_lon / cg.cell,
                                  0, cg.w - 1))
                ccy = int(np.clip((cg.north - plat) * cg.m_per_deg_lat / cg.cell,
                                  0, cg.h - 1))
                drows.append((plat, plon, float(dpark[y, x]), 0.0,
                              plat, plon, int(coarse_comp[ccy, ccx])))
            if drows:
                os.makedirs(DRIVE_DIR, exist_ok=True)
                np.save(f"{DRIVE_DIR}/q{i:03d}.npy",
                        np.array(drows, dtype=np.float64))

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
    gg = Grid(AUS, LAND_CELL)
    print(f"land mask grid {gg.w} x {gg.h} at {LAND_CELL:.0f} m")
    total, t0 = 0, time.time()
    for i, box in enumerate(cfg["boxes"]):
        if os.path.exists(f"{PEAK_DIR}/p{i:03d}.npy"):
            continue
        n = one_chunk(i, box, ocean, comp, cg, gg)
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

    land_meta = merge_land(out)
    drive_meta = merge_drive(out, comp, remap, cg, sizes)

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
        "land": land_meta, "drive": drive_meta,
    }, open(out, "w"), indent=1)
    for f in (out, out.replace(".json", ".bin"), out.replace(".json", "-comp.bin")):
        print(f"  -> {f}  {os.path.getsize(f)/1024:,.0f} KB")


def merge_land(out):
    """Assemble the per-chunk land patches into one 1 km mask, shipped as a 1-bit PNG.

    PNG rather than a raw bitmap because a land mask is enormous flat regions and
    deflate eats it; the browser decodes it with the image decoder it already has and
    reads the pixels off a canvas. Raw it is 2 MB.
    """
    from PIL import Image
    gg = Grid(AUS, LAND_CELL)
    land = np.zeros((gg.h, gg.w), bool)
    seen = 0
    for f in sorted(os.listdir(LAND_DIR)) if os.path.isdir(LAND_DIR) else []:
        d = np.load(f"{LAND_DIR}/{f}")
        x0, y0, patch = int(d["x0"]), int(d["y0"]), d["land"]
        land[y0:y0 + patch.shape[0], x0:x0 + patch.shape[1]] |= patch
        seen += 1
    path = out.replace("peaks.json", "land.png")
    Image.fromarray((land * 255).astype(np.uint8), "L").convert("1").save(
        path, optimize=True)
    pct = 100.0 * land.sum() / land.size
    print(f"  land mask {gg.w} x {gg.h} from {seen} patches, {pct:.1f}% land "
          f"-> {path}  {os.path.getsize(path)/1024:,.0f} KB")
    s_, w_, n_, e_ = AUS
    return {"width": gg.w, "height": gg.h, "south": s_, "west": w_,
            "north": n_, "east": e_, "cell_m": LAND_CELL, "file": "land.png"}


def merge_drive(out, comp, remap, cg, sizes):
    """Pack the drive-only peaks - the emptiest place you can park."""
    if not os.path.isdir(DRIVE_DIR):
        print("  no drive peaks mined")
        return None
    rows = [np.load(f"{DRIVE_DIR}/{f}") for f in sorted(os.listdir(DRIVE_DIR))]
    a = np.concatenate(rows)
    print(f"  {len(a):,} raw drive peaks")

    # Same two-tier prune as the main set, and for the same reason: without the thinned
    # tail a drive from the middle of a city has no answer at all. The road network is
    # far denser than the track-ends the main field finds, so both numbers are looser.
    KEEP_ALL_ABOVE, SPACING_DEG = 2000.0, 5.0 / 111.0
    strong = a[a[:, 2] >= KEEP_ALL_ABOVE]
    weak = a[a[:, 2] < KEEP_ALL_ABOVE]
    weak = weak[np.argsort(-weak[:, 2])]
    key = (np.floor(weak[:, 0] / SPACING_DEG).astype(np.int64) * 1_000_000
           + np.floor(weak[:, 1] / SPACING_DEG).astype(np.int64))
    _, first = np.unique(key, return_index=True)
    a = np.concatenate([strong, weak[np.sort(first)]])
    a = a[np.argsort(-a[:, 2])]
    print(f"  {len(strong):,} at or above {KEEP_ALL_ABOVE:.0f} m, "
          f"{len(first):,} thinned below it -> {len(a):,} shipped")
    print(f"  best park {a[0,2]/1000:.1f} km at {a[0,0]:.4f}, {a[0,1]:.4f}")

    px = np.clip(((a[:, 1] - cg.west) * cg.m_per_deg_lon / cg.cell).astype(int),
                 0, cg.w - 1)
    py = np.clip(((cg.north - a[:, 0]) * cg.m_per_deg_lat / cg.cell).astype(int),
                 0, cg.h - 1)
    cid = remap[np.clip(comp[py, px], 0, sizes.size - 1)]

    blob = b"".join([
        np.rint(a[:, 0] * 1e5).astype("<i4").tobytes(),
        np.rint(a[:, 1] * 1e5).astype("<i4").tobytes(),
        np.rint(np.clip(a[:, 2] / DIST_SCALE_M, 0, 65535)).astype("<u2").tobytes(),
        np.zeros(len(a), dtype="<u2").tobytes(),
        np.rint(a[:, 0] * 1e5).astype("<i4").tobytes(),   # access point IS the spot
        np.rint(a[:, 1] * 1e5).astype("<i4").tobytes(),
        cid.astype("<u2").tobytes(),
    ])
    path = out.replace("peaks.json", "peaks-drive.bin")
    open(path, "wb").write(blob)
    print(f"  -> {path}  {os.path.getsize(path)/1024:,.0f} KB")
    return {"count": int(len(a)), "max_m": float(a[0, 2]),
            "file": "peaks-drive.bin",
            "measured_to": sorted(BUILT), "on": "road"}


if __name__ == "__main__":
    {"coarse": coarse, "chunks": chunks_stage, "merge": merge}[sys.argv[1]]()
