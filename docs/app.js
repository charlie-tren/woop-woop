/* Woop Woop - the emptiest place you can actually get to.
 *
 * The browser does not hold a distance surface. It holds the PEAKS of one.
 *
 * The answer to "the furthest point in this region" is always a local maximum, so the
 * only cells that can ever be an answer are the peaks - a few tens of thousands, not a
 * few billion. Everything a query needs is precomputed into each peak by the build:
 * how far it is from civilisation, how far off a track it sits, where you leave the
 * car, and which landmass it is on. The query is then a scan over a sorted list.
 *
 * That is what makes coverage a matter of running the build rather than inventing a
 * tile pyramid.
 */
const DATA = "data/";

// Average progress, not top speed - a car does not hold 100 km/h on the way out of
// town. Marked as an estimate in the UI because it IS one: the real version asks a
// routing engine which roads exist and how fast they are.
// Fitted against 84 real isochrones, scoring on the ANSWER each radius produces
// rather than on any geometric proxy. Two things came out of that:
//
// 1. No radius reproduces the isochrone's answer - best agreement was 14% walking,
//    41% riding, 32% driving. A circle simply cannot express a road network, which is
//    why the real check is now the default rather than an extra.
// 2. The old numbers over-promised badly: at the previous settings the estimate named
//    an unreachable spot in 24 of 28 driving cases. These are the values that minimise
//    that, so when the estimate IS used it errs towards under-promising.
const MODES = {
  foot: { label: "Walk", verb: "walk", kmh: 1.8, detour: 1.0 },
  bike: { label: "Ride", verb: "ride", kmh: 7.3, detour: 1.0 },
  car: { label: "Drive", verb: "drive", kmh: 22.0, detour: 1.0 },
};

// Every answer sits on a track or footpath, and the last stretch of it is walked
// whatever you arrived in. openrouteservice walks at about 5 km/h, measured in the
// calibration, so the walk-in leg is costed at that rather than at the mode's speed.
const WALK_KMH = 5.0;

/* Whether the vehicle reaches the answer itself, or stops short of it.
 *
 * Walking always reaches it: a peak sits on a track, path or footway by construction.
 * Driving never does - driving-car will not route down a footway - so the polygon has
 * to be tested against the ACCESS point and the remaining metres walked. Testing the
 * spot instead is what made a 60 minute drive take well over an hour.
 *
 * Unless the walk is switched off, in which case the whole question changes and so does
 * the peak file: see activeSet(). */
function walksToSpot() {
  return state.mode === "foot" || (!state.walkLeg && !!PD);
}

/* Which peak file answers the current question.
 *
 * These are not the same measurement. The main file maximises distance from ANYTHING,
 * roads included, so its answers sit at the end of fire trails and footpaths. A point
 * on a road is 0 m from anything by that definition, so it can never answer "where can
 * I drive to" - which is why the drive-only file maximises a different field, with the
 * road excluded from the measurement but required underfoot. The headline changes
 * wording with the file, because it is a different claim. */
function activeSet() {
  return state.mode !== "foot" && !state.walkLeg && PD ? PD : P;
}

// Per-PROFILE ceilings, not one global number. Measured against the live Worker on
// 31/08/2026: foot and bike both return 200 at 120 and 240 minutes, car is refused
// above 3600 s with error 3004. openrouteservice's own restrictions page agrees -
// foot to 20 hours, cycling to 5, driving to 1. The previous single ISO_MAX_MINUTES
// of 60 applied the DRIVING limit to all three, so walking and riding had been
// silently dropping to the circle above an hour for no reason.
const ISO_MAX_MINUTES = { foot: 300, bike: 300, car: 60 };

const state = {
  mode: "foot", mins: 60,
  // Sydney, not Brisbane. Changed 31/08/2026 - the homepage card is a picture of
  // whatever this opens on, and the site is written from Sydney.
  origin: { lat: -33.8688, lon: 151.2093 },
  walkLeg: true,         // willing to walk the last stretch off the road network
  bands: null,           // [{mins, rings}] innermost first, once fetched
  isoNote: "",           // why the real network is not being used, if it is not
  busy: false,
  pick: null,            // the candidate a real route confirmed, once one has
  pickKey: "",           // the question that pick was made for; see queryKey()
  verifyNote: "",        // said aloud only when no candidate actually fitted
};

// The Worker holds the openrouteservice key. The page never sees it.
const ISO_URL = "https://woop-woop-iso.charlie-tren.workers.dev";

// Keyed the same way the Worker rounds - about 110 m - so nudging the map or the
// slider back to somewhere already asked about costs nothing. The free plan allows
// 500 isochrones a DAY, and a slider dragged across its range would spend fifty.
const isoCache = new Map();

const isoKey = (o, mode, mins) =>
  mode + "|" + mins + "|" + o.lat.toFixed(3) + "|" + o.lon.toFixed(3);

async function fetchIsochrone(origin, mode, mins) {
  const k = isoKey(origin, mode, mins);
  if (isoCache.has(k)) return isoCache.get(k);
  const res = await fetch(ISO_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: mode, lat: origin.lat, lon: origin.lon,
                           seconds: mins * 60 }),
  });
  if (!res.ok) {
    // 429 is the daily quota; anything else out here is usually openrouteservice
    // failing to route from a remote track, which the calibration hit 6 times in 108.
    // 400 from the Worker is its own range guard, which is a DIFFERENT thing from a
    // routing failure and reads wrong as one. It also fires whenever the page asks for
    // longer than the deployed Worker allows, which is exactly the window between
    // raising the slider here and deploying the Worker that permits it.
    const err = new Error(res.status === 429 ? "quota"
      : res.status === 400 ? "range" : "route");
    err.code = res.status;
    throw err;
  }
  const geo = await res.json();
  const rings = geo.features[0].geometry.coordinates;
  isoCache.set(k, rings);
  return rings;
}

/* Ray casting, outer ring minus holes. The isochrone is the real reachable set, so
 * "can I get there" stops being a radius and becomes a containment test. */
function inRing(lat, lon, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    if ((yi > lat) !== (yj > lat) &&
        lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function inIsochrone(lat, lon, rings) {
  if (!inRing(lat, lon, rings[0])) return false;
  for (let r = 1; r < rings.length; r++) {
    if (inRing(lat, lon, rings[r])) return false;   // a hole
  }
  return true;
}

/* Bands, not one shape.
 *
 * A single polygon at the full budget answers "can I get there in an hour", which is
 * the wrong question once part of the trip is walked. What is needed is an upper bound
 * on how long the DRIVEN part took, so the walk can be charged against what is left.
 * Nested isochrones give that: the innermost band containing the access point bounds
 * the drive, and the walk has to fit in the remainder.
 *
 * openrouteservice takes several ranges in ONE call, which is what this should be.
 * The deployed Worker still builds `range: [seconds]` from a single number, so until
 * that ships these are separate requests fired in parallel - N times the quota, which
 * both the client Map and the Worker's 24 h edge cache absorb on any repeat view.
 */
function bandMinutes(mins) {
  const n = mins <= 20 ? 2 : 4;
  const out = [];
  for (let i = 1; i <= n; i++) out.push(Math.max(1, Math.round((mins * i) / n)));
  return [...new Set(out)];
}

async function fetchBands(origin, mode, mins) {
  const wanted = bandMinutes(mins);
  const rings = await Promise.all(
    wanted.map((b) => fetchIsochrone(origin, mode, b)));
  return wanted.map((b, i) => ({ mins: b, rings: rings[i],
                                 bb: ringBounds(rings[i][0]) }));
}

/* The innermost band containing a point, i.e. the tightest upper bound on the time to
 * travel there. Bands are ordered smallest first, so the first hit is the answer. */
function bandFor(lat, lon, bands) {
  for (const b of bands) {
    if (lat >= b.bb.s && lat <= b.bb.n && lon >= b.bb.w && lon <= b.bb.e &&
        inIsochrone(lat, lon, b.rings)) {
      return b;
    }
  }
  return null;
}

function ringBounds(ring) {
  let s = 90, w = 180, n = -90, e = -180;
  for (const p of ring) {
    if (p[1] < s) s = p[1];
    if (p[1] > n) n = p[1];
    if (p[0] < w) w = p[0];
    if (p[0] > e) e = p[0];
  }
  return { s: s, w: w, n: n, e: e };
}
let meta, P, PD, comp, map, layers = {};

/* ---------- the coastline, read off the basemap ---------- */
/* The 250 m mask below is the fallback, not the first choice, because 250 m cannot draw
 * a harbour. Over Sydney it called 22% of the water window land where the map's own
 * pixels say 42%: the whole CBD and Potts Point shoreline came back as sea, and what did
 * survive was a staircase of 250 m squares roughly 23 screen pixels across.
 *
 * The basemap is already a land/water rendering at about 5 m a pixel, it is already in
 * the browser cache because the map is showing it, and tile.openstreetmap.org serves it
 * with Access-Control-Allow-Origin *, so the pixels can be read back. Water in the OSM
 * "standard" style is a flat #aad3df and nothing else on the map is near it.
 *
 * Measured over Sydney Harbour at z15 before this was written: colour alone leaves 4,289
 * separate land blobs in open water, because the ferry route dashes and the place labels
 * are drawn ON TOP of the sea and are not water-coloured. One majority pass over a 9x9
 * box takes that to 158 while costing 0.37% of the water area, so it erases lettering and
 * dashed lines without eating real headlands. The box is summed from an integral image,
 * which makes the kernel size free.
 */
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const WATER_RGB = [170, 211, 223];
/* PER CHANNEL, not the sum of the three.
 *
 * The sum was 60 and it was catching whole land uses, because it treats a colour that is
 * far off in ONE channel the same as one slightly off in all three. Measured over the
 * tiles Charlie was looking at:
 *
 *   Rookwood Cemetery  #aacbaf  sum 56  - identical red, near-identical green, blue out
 *                                         by 48. 62,468 pixels of cemetery read as water
 *                                         and the whole cemetery went unfilled.
 *   airport apron      #bbbbcc  sum 60  - exactly on the old threshold.
 *   grey buildings     #d4d3d3  sum 54  - neutral grey, equidistant in every channel,
 *                                         which is where most of the pixel speckle came
 *                                         from as well.
 *
 * Requiring every channel within 20 rejects all three while still accepting water and
 * its anti-aliased edges (#b1c9d3, #b2dbda). Measured share classified water, before and
 * after: Rookwood 31.14% -> 0.48%, the airport 26.48% -> 12.32% (it really does have the
 * Cooks River and Botany Bay on it), Sydney Harbour 79.64% -> 77.58%.
 */
const WATER_CH_TOL = 20;
/* Water that has been LIGHTENED, which the per-channel test alone misses.
 *
 * Charlie, 06/09/2026: patches around the ponds in Centennial Park. The ponds were being
 * caught in the middle and filled around the edges, and the missed pixels turned out to
 * be #c4dce7, #dbecf1, #d5e8ea - water blended toward white. Two things do that: the
 * white halo the map paints behind a label, which is why the patches sat over the words
 * "Duck Pond" and "Busbys Pond", and the anti-aliased rim where a pond meets the park.
 *
 * Blending toward white moves each channel a FRACTION of its own distance to 255, so the
 * test is that fraction: work out f per channel, and accept when all three agree. Pure
 * white has f = 1 in every channel and is rejected by the ceiling, which matters because
 * the map is full of white. A neutral grey has wildly disagreeing f (0.63, 0.27, 0.00 for
 * #e0dfdf) and fails the spread.
 *
 * Verified against thirteen real carto colours: accepts water, its anti-aliasing, pale
 * water and all three halo tints; rejects cemetery, airport apron, grey building,
 * residential, park grass, forest and pure white.
 */
const WHITE_F_MAX = 0.80;
const WHITE_F_SPREAD = 0.22;
const WHITE_F_MIN = -0.10;
const MAJORITY_K = 9;
const MAX_TILES = 32;      // raised with the margin above, to hold the same detail

const lon2tx = (lon, z) => ((lon + 180) / 360) * Math.pow(2, z);
const lat2ty = (lat, z) => {
  const r = (lat * Math.PI) / 180;
  return ((1 - Math.asinh(Math.tan(r)) / Math.PI) / 2) * Math.pow(2, z);
};
const ty2lat = (y, z) => {
  const n = Math.PI * (1 - (2 * y) / Math.pow(2, z));
  return (180 / Math.PI) * Math.atan(Math.sinh(n));
};

function loadTile(z, x, y) {
  return new Promise((resolve) => {
    const img = new Image();
    // Without this the canvas is tainted and getImageData throws, which is the whole
    // reason this can work at all.
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = TILE_URL.replace("{z}", z).replace("{x}", x).replace("{y}", y);
  });
}

/* True where the pixel is water, after the majority pass. Uint8Array, W*H. */
function waterFromPixels(data, W, H) {
  const raw = new Uint8Array(W * H);
  const [wr, wg, wb] = WATER_RGB;
  const t = WATER_CH_TOL;
  const dr = 255 - wr, dg = 255 - wg, db = 255 - wb;
  for (let i = 0, j = 0; i < raw.length; i++, j += 4) {
    const r = data[j], g = data[j + 1], b = data[j + 2];
    if (Math.abs(r - wr) <= t && Math.abs(g - wg) <= t && Math.abs(b - wb) <= t) {
      raw[i] = 1;
      continue;
    }
    // ...or the same colour lightened towards white by one common fraction.
    const fr = (r - wr) / dr, fg = (g - wg) / dg, fb = (b - wb) / db;
    const lo = Math.min(fr, fg, fb), hi = Math.max(fr, fg, fb);
    if (lo >= WHITE_F_MIN && hi <= WHITE_F_MAX && hi - lo <= WHITE_F_SPREAD) {
      raw[i] = 1;
    }
  }
  // Integral image over the raw water field, edge-clamped by clamping the lookups.
  const ii = new Int32Array((W + 1) * (H + 1));
  for (let y = 0; y < H; y++) {
    let run = 0;
    for (let x = 0; x < W; x++) {
      run += raw[y * W + x];
      ii[(y + 1) * (W + 1) + x + 1] = ii[y * (W + 1) + x + 1] + run;
    }
  }
  const k = MAJORITY_K, h = k >> 1, need = k * k;
  const out = new Uint8Array(W * H);
  for (let y = 0; y < H; y++) {
    const y0 = Math.max(0, y - h), y1 = Math.min(H, y + h + 1);
    for (let x = 0; x < W; x++) {
      const x0 = Math.max(0, x - h), x1 = Math.min(W, x + h + 1);
      const tot = ii[y1 * (W + 1) + x1] - ii[y0 * (W + 1) + x1]
                - ii[y1 * (W + 1) + x0] + ii[y0 * (W + 1) + x0];
      // Compare against the area actually sampled, so the edge of the mosaic is not
      // biased towards land just for having fewer neighbours.
      const area = (y1 - y0) * (x1 - x0);
      if (tot * 2 >= area) out[y * W + x] = 1;
    }
  }
  return out;
}

/* Paint the fill from basemap tiles. Resolves to Leaflet bounds, or null to fall back.
 *
 * The mosaic is built in tile space and handed to Leaflet with its own tile-aligned
 * corners, which is exactly right: an image overlay stretches linearly between projected
 * corners, and Mercator tiles are linear in projected space. The 250 m painter samples on
 * a lat/lon grid instead and leans on Mercator being near-linear over a city. */
async function paintLandFillFromTiles(rings, win, zoomHint) {
  const bb = win;
  if (!bb || bb.e <= bb.w || bb.n <= bb.s) return null;
  let z = Math.max(9, Math.min(17, Math.round(zoomHint || 13)));
  let x0, x1, y0, y1;
  for (; z >= 9; z--) {
    x0 = Math.floor(lon2tx(bb.w, z)); x1 = Math.floor(lon2tx(bb.e, z));
    y0 = Math.floor(lat2ty(bb.n, z)); y1 = Math.floor(lat2ty(bb.s, z));
    if ((x1 - x0 + 1) * (y1 - y0 + 1) <= MAX_TILES) break;
  }
  if (z < 9) return null;

  const cols = x1 - x0 + 1, rows = y1 - y0 + 1;
  const tiles = await Promise.all(
    [].concat(...Array.from({ length: cols }, (_, i) =>
      Array.from({ length: rows }, (_, j) => loadTile(z, x0 + i, y0 + j).then(
        (img) => ({ img: img, i: i, j: j })))))
  );
  if (tiles.some((t) => !t.img)) return null;

  const W = cols * 256, H = rows * 256;
  const src = document.createElement("canvas");
  src.width = W; src.height = H;
  const sctx = src.getContext("2d", { willReadFrequently: true });
  for (const t of tiles) sctx.drawImage(t.img, t.i * 256, t.j * 256);

  let px;
  try {
    px = sctx.getImageData(0, 0, W, H).data;     // throws if a tile blocked CORS
  } catch (e) {
    return null;
  }
  const water = waterFromPixels(px, W, H);

  const off = document.createElement("canvas");
  off.width = W; off.height = H;
  const octx = off.getContext("2d");
  const id = octx.createImageData(W, H);
  const d = id.data;
  for (let i = 0, j = 0; i < water.length; i++, j += 4) {
    if (!water[i]) { d[j] = 0xc2; d[j + 1] = 0x45; d[j + 2] = 0x1f; d[j + 3] = 0x59; }
  }
  octx.putImageData(id, 0, 0);

  const north = ty2lat(y0, z), south = ty2lat(y1 + 1, z);
  const west = (x0 / Math.pow(2, z)) * 360 - 180;
  const east = ((x1 + 1) / Math.pow(2, z)) * 360 - 180;

  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const ctx = c.getContext("2d");
  ctx.beginPath();
  for (const ring of rings) {
    for (let i = 0; i < ring.length; i++) {
      const x = (lon2tx(ring[i][0], z) - x0) * 256;
      const y = (lat2ty(ring[i][1], z) - y0) * 256;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
  }
  ctx.clip("evenodd");
  ctx.drawImage(off, 0, 0);
  off.width = off.height = 0; src.width = src.height = 0;

  const blob = await new Promise((res) => c.toBlob(res));
  if (!blob) return null;
  if (fillURL) URL.revokeObjectURL(fillURL);
  fillURL = URL.createObjectURL(blob);
  return L.latLngBounds([[south, west], [north, east]]);
}

/* ---------- the land mask ---------- */
/* One bit per 250 m cell of the continent, shipped as packed bits.
 *
 * It exists because the isochrone is a generalised hull: where two reachable shores face
 * each other it spans the water between them, and the fill was being painted over Sydney
 * Harbour. The 4 km component grid already shipped cannot resolve a harbour at all, and
 * at 1 km every point in the channel still read as land - it is only about 1.5 km across.
 *
 * Packed bits rather than a PNG on purpose: a PNG would have to go through a canvas to
 * be read, and 67 megapixels of ImageData is a quarter of a gigabyte for one bit a cell.
 * This is 8.3 MB, gzipped by Pages on the way out, and indexed directly.
 */
let landBits = null, landG = null;

async function loadLand() {
  if (!meta.land) return;
  const g = Object.assign({}, meta.land);
  // Reproduce the build's own projection rather than normalising by the bbox: Grid
  // rounds its width UP to a whole cell, so dividing by the span drifts by a fraction
  // of a cell across the continent.
  const mLat = (Math.PI * 6371000) / 180;
  g.mLon = mLat * Math.cos((((g.south + g.north) / 2) * Math.PI) / 180);
  g.mLat = mLat;
  landG = g;

  const buf = await (await fetch(DATA + g.file)).arrayBuffer();
  const need = ((g.width * g.height) + 7) >> 3;
  if (buf.byteLength !== need) {
    throw new Error("land mask is " + buf.byteLength + " bytes, manifest wants " + need);
  }
  landBits = new Uint8Array(buf);
}

function landAt(lat, lon) {
  const g = landG;
  if (!landBits || !g) return true;
  const x = Math.floor(((lon - g.west) * g.mLon) / g.cell_m);
  const y = Math.floor(((g.north - lat) * g.mLat) / g.cell_m);
  if (x < 0 || y < 0 || x >= g.width || y >= g.height) return false;
  const i = y * g.width + x;
  return !!((landBits[i >> 3] >> (i & 7)) & 1);
}

function landBit(g, x, y) {
  if (x < 0 || y < 0 || x >= g.width || y >= g.height) return 0;
  const i = y * g.width + x;
  return (landBits[i >> 3] >> (i & 7)) & 1;
}

/* The same mask read as a FIELD rather than as cells.
 *
 * The fill is drawn at roughly 10 m a pixel over a city, against a mask whose cells are
 * 250 m, so sampling it nearest-neighbour paints the coastline as a staircase of ~23 px
 * squares - which is what a harbour edge looked like. Interpolating between the four
 * surrounding cells and cutting at 0.5 gives a straight edge across each cell instead of
 * a corner, so the boundary follows the same 250 m data without quantising the DISPLAY
 * to it.
 *
 * This does not claim to know the coast to better than 250 m, and nothing that decides
 * an ANSWER uses it - landAt above still does that, on whole cells. This is the painter
 * only, where the job is to not look like a bar chart of the sea.
 */
function landFrac(lat, lon) {
  const g = landG;
  if (!landBits || !g) return 1;
  const fx = ((lon - g.west) * g.mLon) / g.cell_m - 0.5;
  const fy = ((g.north - lat) * g.mLat) / g.cell_m - 0.5;
  const x0 = Math.floor(fx), y0 = Math.floor(fy);
  const tx = fx - x0, ty = fy - y0;
  const a = landBit(g, x0, y0), b = landBit(g, x0 + 1, y0);
  const c = landBit(g, x0, y0 + 1), d = landBit(g, x0 + 1, y0 + 1);
  return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty;
}

/* The fill, as an image: land inside the isochrone, nothing outside it and nothing on
 * the water. Painted into an offscreen canvas and clipped by the polygon path, because
 * putImageData ignores a clip region and drawImage honours it. Handed to Leaflet as an
 * overlay so panning and zooming stay its problem rather than ours. */
const FILL_MAX_PX = 1600;
let fillURL = null;

function paintLandFill(rings, done) {
  const bb = ringBounds(rings[0]);
  const spanLon = bb.e - bb.w, spanLat = bb.n - bb.s;
  if (spanLon <= 0 || spanLat <= 0) return done(null);
  const aspect = (spanLon * landG.mLon) / (spanLat * landG.mLat);
  let W = FILL_MAX_PX, H = Math.round(FILL_MAX_PX / aspect);
  if (H > FILL_MAX_PX) { H = FILL_MAX_PX; W = Math.round(FILL_MAX_PX * aspect); }
  W = Math.max(2, W); H = Math.max(2, H);

  // Land, as pixels. No polygon test in here - that is what the clip is for.
  const off = document.createElement("canvas");
  off.width = W; off.height = H;
  const octx = off.getContext("2d");
  const id = octx.createImageData(W, H);
  const d = id.data;
  for (let py = 0; py < H; py++) {
    const lat = bb.n - ((py + 0.5) / H) * spanLat;
    for (let px2 = 0; px2 < W; px2++) {
      const lon = bb.w + ((px2 + 0.5) / W) * spanLon;
      if (landFrac(lat, lon) >= 0.5) {
        const o = (py * W + px2) * 4;
        d[o] = 0xc2; d[o + 1] = 0x45; d[o + 2] = 0x1f; d[o + 3] = 0x59;
      }
    }
  }
  octx.putImageData(id, 0, 0);

  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const ctx = c.getContext("2d");
  ctx.beginPath();
  for (const ring of rings) {
    for (let i = 0; i < ring.length; i++) {
      const x = ((ring[i][0] - bb.w) / spanLon) * W;
      const y = ((bb.n - ring[i][1]) / spanLat) * H;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
  }
  ctx.clip("evenodd");
  ctx.drawImage(off, 0, 0);
  off.width = off.height = 0;

  c.toBlob((blob) => {
    if (fillURL) URL.revokeObjectURL(fillURL);
    fillURL = blob ? URL.createObjectURL(blob) : null;
    done(fillURL && L.latLngBounds([[bb.s, bb.w], [bb.n, bb.e]]));
  });
}

const $ = (s) => document.querySelector(s);
const fmtKm = (m) => (m < 950 ? Math.round(m) + " m" : (m / 1000).toFixed(1) + " km");

/* Structure of arrays, in the order the build wrote them. Both peak files share this
 * layout, so one reader serves both. */
function readPeaks(buf, n) {
  let o = 0;
  const take = (Type, count) => {
    const a = new Type(buf, o, count); o += count * Type.BYTES_PER_ELEMENT; return a;
  };
  const lat = take(Int32Array, n), lon = take(Int32Array, n);
  const d = take(Uint16Array, n), off = take(Uint16Array, n);
  const alat = take(Int32Array, n), alon = take(Int32Array, n);
  const c = take(Uint16Array, n);
  return { n: n, s: meta.coord_scale, ds: meta.dist_scale_m || 1,
           lat: lat, lon: lon, d: d, off: off, alat: alat, alon: alon, c: c };
}

async function load() {
  meta = await (await fetch(DATA + "peaks.json")).json();
  P = readPeaks(await (await fetch(DATA + "peaks.bin")).arrayBuffer(), meta.count);

  // The drive-only file and the land mask are both additions; an older deploy of the
  // data has neither, and the page has to work without them rather than throw.
  if (meta.drive && meta.drive.count) {
    try {
      PD = readPeaks(await (await fetch(DATA + meta.drive.file)).arrayBuffer(),
                     meta.drive.count);
    } catch (e) { PD = null; }
  }
  try { await loadLand(); } catch (e) { landBits = null; }

  // One byte per cell unless the manifest says otherwise. The component ids are
  // renumbered by landmass size at build time, so a byte covers every landmass anyone
  // can actually reach - reading this as 16-bit silently halves the grid and every
  // lookup lands in the wrong hemisphere.
  const cb = await (await fetch(DATA + "peaks-comp.bin")).arrayBuffer();
  comp = (meta.comp.bytes === 2) ? new Uint16Array(cb) : new Uint8Array(cb);
  const need = meta.comp.width * meta.comp.height;
  if (comp.length !== need) {
    throw new Error("component grid is " + comp.length + " cells, manifest says " + need);
  }
}

/* Which landmass a point is on. Islands get their own id, so "can I get there without
 * a boat" is an integer comparison rather than a flood fill over a raster we no longer
 * ship. Returns 0 for water, which matches nothing. */
function componentAt(lat, lon) {
  const g = meta.comp;
  const x = Math.floor(((lon - g.west) / (g.east - g.west)) * g.width);
  const y = Math.floor(((g.north - lat) / (g.north - g.south)) * g.height);
  if (x < 0 || y < 0 || x >= g.width || y >= g.height) return 0;
  return comp[y * g.width + x];
}

/* Which landmass to answer for, when the exact cell is unhelpful.
 *
 * Component ids are ranked by landmass size at build time - 1 is the mainland - so
 * taking the LOWEST non-zero id nearby means "the biggest landmass within reach of
 * this point". Taking the nearest non-zero instead put Brisbane on component 150, a
 * sand island offshore, because a 4 km grid cell centred on the CBD lands in the river
 * mouth; every answer then came from that island and more time changed nothing.
 */
function componentNear(lat, lon) {
  const g = meta.comp;
  const stepLat = (g.north - g.south) / g.height;
  const stepLon = (g.east - g.west) / g.width;
  let best = 0;
  for (let r = 0; r <= 25; r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (r > 0 && Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        const v = componentAt(lat - dy * stepLat, lon + dx * stepLon);
        if (v && (best === 0 || v < best)) best = v;
      }
    }
    // Keep widening a little past the first hit, so a genuine island beside the
    // mainland does not win purely by being one cell closer.
    if (best && r >= 3) return best;
  }
  return best;
}

/* ---------- painting the fill for what is on screen ---------- */
/* The fill used to be rendered ONCE, at a zoom chosen from the whole isochrone, and then
 * stretched as you zoomed in. That capped its detail at the first render regardless of
 * how close you got: a 60 minute walk across Sydney spans about 0.1 degrees, which fits
 * in 24 tiles only at z13, or 15.9 metres per pixel. Alexandria Canal is roughly 20 m
 * wide, so it was about ONE pixel in the source tiles and disappeared - which is exactly
 * what Charlie saw, a canal covered by the fill while the wide basin beside it was not.
 *
 * So the window is now the part of the isochrone actually on screen, drawn at the zoom
 * you are actually looking at. The tile budget then buys detail where you are looking
 * instead of being spent covering country that is off the edge of the map, and the same
 * 24 tiles give 2 m per pixel at z16.
 */
let fillRings = null, fillTimer = null;

function fillWindow(padding) {
  if (!fillRings) return null;
  const bb = ringBounds(fillRings[0]);
  // Generous, because the cost of being too small is VISIBLE: the overlay ends in a
  // straight line across the map. 0.25 covers an ordinary drag and most of a zoom step.
  const v = map.getBounds().pad(padding == null ? 0.25 : padding);
  const win = { s: Math.max(bb.s, v.getSouth()), w: Math.max(bb.w, v.getWest()),
                n: Math.min(bb.n, v.getNorth()), e: Math.min(bb.e, v.getEast()) };
  return (win.e > win.w && win.n > win.s) ? win : null;
}

/* The part of the screen the fill actually has to cover: the isochrone, cropped to the
 * viewport. NOT the viewport - the image is clipped to the shape, so whenever the shape
 * is smaller than the screen (which is most of the time) an image that covers everything
 * it should still fails a naive "does it cover the view" test, and the fill would be
 * thrown away the instant it was drawn. */
function fillNeeded() {
  const w = fillWindow(0);
  return w ? L.latLngBounds([[w.s, w.w], [w.n, w.e]]) : null;
}

function drawFill() {
  if (!fillRings || !landBits) return;
  const seq = ++fillSeq;
  const win = fillWindow();
  if (!win) {                       // the shape is off screen entirely
    if (layers.fill) { map.removeLayer(layers.fill); layers.fill = null; }
    return;
  }
  const show = (bounds) => {
    if (seq !== fillSeq || !bounds || !fillURL) return true;
    if (layers.fill) map.removeLayer(layers.fill);
    layers.fill = L.imageOverlay(fillURL, bounds,
      { opacity: 1, interactive: false, className: "iso-fill" }).addTo(map);
    return true;
  };
  // Basemap pixels first, the 250 m mask only if that cannot be done - offline, a tile
  // that will not load, or a canvas the browser refuses to read back.
  paintLandFillFromTiles(fillRings, win, map.getZoom())
    .catch(() => null)
    .then((bounds) => {
      if (seq !== fillSeq) return;
      if (bounds) { show(bounds); return; }
      paintLandFill(fillRings, show);
    });
}

/* Panning and zooming re-cut the window, so the fill is redrawn - debounced, because a
 * drag fires moveend once but a pinch fires it repeatedly, and each redraw reads back a
 * canvas of a few megapixels. */
function scheduleFill() {
  clearTimeout(fillTimer);
  fillTimer = setTimeout(drawFill, 200);
}

/* ---------- the query ---------- */
function solve(limit) {
  const m = MODES[state.mode];
  const mPerDegLat = 111320;
  const mPerDegLon = mPerDegLat * Math.cos((state.origin.lat * Math.PI) / 180);
  const want = componentNear(state.origin.lat, state.origin.lon);
  if (!want) return null;

  const bands = state.bands;
  const toSpot = walksToSpot();
  const Q = activeSet();

  const picked = limit ? [] : null;
  let nearest = null, nearestM = Infinity;
  for (let i = 0; i < Q.n; i++) {
    if (Q.c[i] !== want) continue;
    const lat = Q.lat[i] / Q.s, lon = Q.lon[i] / Q.s;
    const alat = Q.alat[i] / Q.s, alon = Q.alon[i] / Q.s;
    const distM = Q.d[i] * Q.ds;
    const dx = (lon - state.origin.lon) * mPerDegLon;
    const dy = (lat - state.origin.lat) * mPerDegLat;
    const away = Math.sqrt(dx * dx + dy * dy);

    // The leg you cover on foot regardless of what you arrived in. Walking mode routes
    // to the spot itself, so it has no separate leg; the wheeled modes stop at the
    // last built ground and walk the rest.
    const walkM = toSpot ? 0 : distM;
    const walkMins = (walkM / (WALK_KMH * 1000)) * 60;

    let reachable, travelMins = null;
    if (bands) {
      const b = bandFor(toSpot ? lat : alat, toSpot ? lon : alon, bands);
      if (b) travelMins = b.mins;
      reachable = !!b && b.mins + walkMins <= state.mins;
    } else {
      // No real network available. Charge the walk first, then spend what is left on
      // the estimated radius - so the circle cannot promise a spot whose walk-in alone
      // would blow the budget.
      const left = state.mins - walkMins;
      reachable = left > 0 &&
                  away <= m.kmh * 1000 * (left / 60) * m.detour;
    }

    const hit = {
      lat: lat, lon: lon, dist_m: distM, offtrack_m: Q.off[i] * Q.ds,
      awayM: away, walkMins: walkMins, travelMins: travelMins,
      exact: !!bands, overBudget: false, access: { lat: alat, lon: alon },
    };
    if (reachable) {
      if (!picked) return hit;
      picked.push(hit);
      if (picked.length >= limit) return picked;
      continue;
    }
    if (away < nearestM) {
      nearestM = away;
      nearest = Object.assign(hit, { overBudget: true });
    }
  }
  return picked && picked.length ? picked : (picked ? [] : nearest);
}

/* ---------- checking the answer against a real route ---------- */
/* The isochrone is a shortlist, not a timetable.
 *
 * Measured 06/09/2026 against Valhalla, sampling the BOUNDARY of the openrouteservice 60
 * minute isochrone - where ORS asserts exactly 60 minutes by construction - a trip ORS
 * calls an hour really takes a median of 1.15 walking and 1.17 riding. That is not a
 * cycling quirk: both modes carry it.
 *
 * The reason there is no correction factor here is that the per-route spread, 0.55 to
 * 1.46 over 32 samples, dwarfs the 15% median. Multiplying every answer by 1.15 would fix
 * the median and make most individual answers worse in one direction or the other. No
 * single number makes an isochrone true about a particular trip.
 *
 * So the isochrone does what it is good at - shortlisting cheaply from one call - and
 * then the ONE point being offered is routed for real. Valhalla's public instance is
 * keyless and sends Access-Control-Allow-Origin *, so the page calls it directly: no key
 * to hide, no Worker in the path, and no dependence on a deploy.
 */
const VALHALLA_URL = "https://valhalla1.openstreetmap.de/route";
const VALHALLA_COSTING = { foot: "pedestrian", bike: "bicycle", car: "auto" };
const VERIFY_MAX = 5;          // candidates to route before giving up, worst case
const realCache = new Map();

async function realMinutes(costing, from, to) {
  const key = costing + "|" + from.lat.toFixed(4) + "," + from.lon.toFixed(4) +
              "|" + to.lat.toFixed(4) + "," + to.lon.toFixed(4);
  if (realCache.has(key)) return realCache.get(key);
  const q = { locations: [{ lat: from.lat, lon: from.lon },
                          { lat: to.lat, lon: to.lon }], costing: costing };
  const res = await fetch(VALHALLA_URL + "?json=" + encodeURIComponent(JSON.stringify(q)));
  if (!res.ok) throw new Error("route");
  const d = await res.json();
  const mins = d.trip.summary.time / 60;
  realCache.set(key, mins);
  return mins;
}

/* Identifies the question being asked, so a reply that arrives after the question
 * changed is discarded rather than answering the wrong one. */
function queryKey() {
  return [state.mode, state.mins, state.origin.lat.toFixed(4),
          state.origin.lon.toFixed(4), state.walkLeg ? 1 : 0,
          state.bands ? state.bands.length : 0].join("|");
}

let verifySeq = 0;

async function verifyAnswer() {
  if (!state.bands) return;                 // an estimate is already flagged as one
  const key = queryKey();
  const seq = ++verifySeq;
  const cands = solve(VERIFY_MAX);
  if (!cands || !cands.length) return;
  const costing = VALHALLA_COSTING[state.mode];
  const toSpot = walksToSpot();
  let firstOver = null;

  for (const c of cands) {
    const target = toSpot ? { lat: c.lat, lon: c.lon } : c.access;
    let ride;
    try {
      ride = await realMinutes(costing, state.origin, target);
    } catch (e) {
      continue;                             // this one cannot be routed; try the next
    }
    if (seq !== verifySeq || key !== queryKey()) return;   // question moved on
    const total = ride + c.walkMins;
    c.realMins = total;
    if (total <= state.mins) {
      state.pick = c; state.pickKey = key; state.verifyNote = "";
      render();
      return;
    }
    if (!firstOver) firstOver = c;
  }
  // Nothing in the shortlist actually fits. Offer the best of them and say so rather
  // than silently presenting a trip that does not.
  if (firstOver) {
    state.pick = firstOver; state.pickKey = key;
    state.verifyNote = "Checked against real roads: the closest this gets is about "
      + fmtMins(firstOver.realMins) + ", which is over your " + fmtMins(state.mins) + ".";
    render();
  }
}

/* How long the trip to a point would actually take, in the chosen mode. */
function minutesFor(m, metres) {
  return metres / (m.kmh * 1000 * m.detour) * 60;
}

function fmtMins(mins) {
  if (mins < 90) return Math.round(mins) + " min";
  const h = mins / 60;
  return (h < 10 ? h.toFixed(1) : Math.round(h)) + " h";
}

/* ---------- rendering ---------- */
function render() {
  const a = (state.pick && state.pickKey === queryKey()) ? state.pick : solve();
  const box = $("#answer");
  $("#origin-ll").textContent =
    state.origin.lat.toFixed(3) + ", " + state.origin.lon.toFixed(3);

  for (const k of ["target", "iso", "leg", "fill"]) {
    if (layers[k]) { map.removeLayer(layers[k]); layers[k] = null; }
  }
  // The outermost band IS the reachable set; the inner ones are only there to time
  // the trip, and drawing them would read as a heat map of nothing.
  //
  // Outline from the polygon, fill from a land-clipped image. The hull spans water
  // wherever two reachable shores face each other, and a shaded harbour reads as
  // somewhere you can go. The boundary still crosses it, because that is what the
  // isochrone actually claims.
  if (state.bands && state.bands.length) {
    const outer = state.bands[state.bands.length - 1];
    const latlngs = outer.rings.map((r) => r.map((p) => [p[1], p[0]]));
    layers.iso = L.polygon(latlngs,
      { color: "#e2674a", weight: 2, opacity: 1,
        fill: !landBits, fillOpacity: 0.22, fillColor: "#c2451f",
        interactive: false }).addTo(map);
    if (landBits) {
      fillRings = outer.rings;
      drawFill();
    }
  }
  if (!a) {
    box.className = "empty";
    box.textContent = "Nothing in range. Try more time, or a start point in "
      + "Australia - that is the extent of the map so far.";
    return;
  }

  const m = MODES[state.mode];
  const gmode = state.mode === "car" ? "driving"
    : state.mode === "bike" ? "bicycling" : "walking";
  // The last leg is on foot only when there IS one; with it switched off the vehicle
  // reaches the spot and the directions must say so.
  const wfrom = walksToSpot() ? state.origin : a.access;
  const wmode = walksToSpot() ? gmode : "walking";
  const gwalk = "https://www.google.com/maps/dir/?api=1&travelmode=" + wmode +
    "&origin=" + wfrom.lat.toFixed(5) + "," + wfrom.lon.toFixed(5) +
    "&destination=" + a.lat.toFixed(5) + "," + a.lon.toFixed(5);
  const gmaps = "https://www.google.com/maps/dir/?api=1&origin=" +
    state.origin.lat.toFixed(5) + "," + state.origin.lon.toFixed(5) +
    "&destination=" + a.access.lat.toFixed(5) + "," + a.access.lon.toFixed(5) +
    "&travelmode=" + gmode;

  box.className = "";
  const over = a.overBudget
    ? '<p class="over">Nothing in range within ' + fmtMins(state.mins) + " " +
      m.verb + " of here. The nearest is <b>" +
      fmtKm(a.awayM) + "</b> away in a straight line.</p>"
    : "";
  const note = state.isoNote ? '<p class="over">' + state.isoNote + "</p>" : "";
  const checked = state.verifyNote && state.pickKey === queryKey()
    ? '<p class="over">' + state.verifyNote + "</p>" : "";

  // The trip, leg by leg. Walking routes to the spot itself; the wheeled modes stop
  // at the last built ground, and the rest is on foot whatever you came in.
  const legs = [];
  const Verb = m.verb.charAt(0).toUpperCase() + m.verb.slice(1);
  // Once a real route has been run, its figure REPLACES the band's rather than sitting
  // beside it. The band is an upper bound from whichever ring the point fell in, so
  // printing both leaves two competing times on the card and makes the reader pick.
  const real = a.realMins != null;
  if (walksToSpot()) {
    legs.push("<li>" + (real
      ? Verb + " there in <b>" + fmtMins(a.realMins) + "</b>, on real roads."
      : a.travelMins === null
        ? Verb + " to <b>" + a.lat.toFixed(4) + ", " + a.lon.toFixed(4) + "</b>."
        : Verb + " there in under <b>" + fmtMins(a.travelMins) + "</b>.")
      + "</li>");
  } else {
    const rideMins = real ? a.realMins - a.walkMins : null;
    const verb = real
      ? "<b>" + fmtMins(rideMins) + "</b> " + m.verb
      : a.travelMins === null
        ? Verb : "Under <b>" + fmtMins(a.travelMins) + "</b> " + m.verb;
    legs.push("<li>" + verb + " to <b>" +
      a.access.lat.toFixed(4) + ", " + a.access.lon.toFixed(4) +
      "</b>, the last built ground.</li>");
    legs.push("<li>Then <b>" + fmtKm(a.dist_m) + "</b> on foot along the track, " +
      "about <b>" + fmtMins(a.walkMins) + "</b>.</li>");
    if (real) {
      legs.push("<li><b>" + fmtMins(a.realMins) + "</b> all up, on real roads.</li>");
    }
  }

  box.innerHTML = note + checked + over +
    '<div class="big">' + fmtKm(a.dist_m) + " <span>" +
      (activeSet() === PD ? "from anything but roads" : "from anything") +
      "</span></div>" +
    '<ul class="leg">' +
    "<li><b>" + a.lat.toFixed(4) + ", " + a.lon.toFixed(4) + "</b></li>" +
    legs.join("") +
    (walksToSpot()
      ? '<li><a target="_blank" rel="noopener" href="' + gwalk + '">Directions</a></li>'
      : '<li><a target="_blank" rel="noopener" href="' + gmaps + '">Directions to the '
        + 'drop-off</a> &middot; <a target="_blank" rel="noopener" href="' + gwalk
        + '">walking directions to the spot</a></li>') +
    "</ul>";

  layers.target = L.circleMarker([a.lat, a.lon], {
    radius: 7, color: "#fff", weight: 2, fillColor: "#e2674a", fillOpacity: 1,
  }).addTo(map).bindTooltip("Furthest from anything");

  // The walked leg, drawn, so the part of the trip that is not on the road network is
  // visible rather than only stated.
  if (!walksToSpot()) {
    layers.leg = L.polyline([[a.access.lat, a.access.lon], [a.lat, a.lon]],
      { color: "#e2674a", weight: 2, dashArray: "4 4", opacity: 0.9,
        interactive: false }).addTo(map);
  }

  const view = L.latLngBounds([[a.lat, a.lon],
                               [state.origin.lat, state.origin.lon]]);
  if (layers.iso) view.extend(layers.iso.getBounds());
  map.fitBounds(view.pad(0.12), { animate: false });
}

function setBusy(on) {
  state.busy = on;
  document.getElementById("map").classList.toggle("busy", on);
}

/* Redraw immediately from the estimate, then fetch the real bands.
 *
 * The API calls are debounced and the drawing is not, so dragging the slider stays
 * responsive and spends at most one round per pause.
 */
let isoTimer = null, isoSeq = 0, fillSeq = 0;

function refresh() {
  // A pick belongs to the question it was made for; anything else is a stale answer.
  if (state.pickKey !== queryKey()) {
    state.pick = null; state.verifyNote = "";
  }
  render();
  clearTimeout(isoTimer);
  // Per-profile ceiling. Only driving is capped at an hour; foot and bike go far
  // further, and treating them as capped is what used to drop them to the circle.
  if (state.mins > ISO_MAX_MINUTES[state.mode]) {
    state.bands = null;
    setBusy(false);
    state.isoNote = "Beyond an hour there is no road-network answer for driving, so "
      + "this is an estimate.";
    render();
    return;
  }
  const seq = ++isoSeq;
  setBusy(true);
  isoTimer = setTimeout(async () => {
    const origin = { lat: state.origin.lat, lon: state.origin.lon };
    const mode = state.mode, mins = state.mins;
    try {
      const bands = await fetchBands(origin, mode, mins);
      if (seq !== isoSeq) return;        // a newer request has overtaken this one
      state.bands = bands;
      state.isoNote = "";
    } catch (err) {
      if (seq !== isoSeq) return;
      state.bands = null;
      state.isoNote = err.message === "quota"
        ? "The routing quota for today is gone, so this is an estimate."
        : err.message === "range"
        ? "That is longer than the routing service will work out, so this is an estimate."
        : "No route could be worked out from here, so this is an estimate.";
    }
    setBusy(false);
    state.pick = null; state.verifyNote = "";
    render();
    // One request per settled answer, not per slider tick: this runs only after the
    // isochrone debounce has already fired and the bands are in.
    verifyAnswer().catch(() => {});
  }, 600);
}

/* ---------- wiring ---------- */
(async function main() {
  await load();

  map = L.map("map", { zoomControl: true, preferCanvas: true })
    .setView([state.origin.lat, state.origin.lon], 8);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 17,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">' +
      "OpenStreetMap</a> contributors",
  }).addTo(map);

  // Imagery sits ON TOP of the map rather than replacing it, so the labels and roads
  // you were just reading do not vanish when you turn it on.
  const sat = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/" +
    "MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 17, opacity: 0.9, attribution: "Imagery &copy; Esri" });
  $("#satellite").addEventListener("change", (e) => {
    if (e.target.checked) sat.addTo(map); else map.removeLayer(sat);
  });

  layers.origin = L.marker([state.origin.lat, state.origin.lon],
    { title: "Start" }).addTo(map);

  // A fill that does not reach the edge of the screen ends in a STRAIGHT LINE, and along
  // a coast that reads as the water boundary being in the wrong place - which is exactly
  // what it looked like. The overlay is only correct where it was drawn, so the moment
  // the view leaves the area it was drawn for, drop it and let the redraw put it back.
  // A second without a fill is honest; a second with a rectangle drawn across the harbour
  // is not.
  const dropUncoveredFill = () => {
    if (!layers.fill) return;
    const need = fillNeeded();
    if (!need || !layers.fill.getBounds().contains(need)) {
      map.removeLayer(layers.fill);
      layers.fill = null;
    }
  };
  map.on("move", dropUncoveredFill);
  map.on("zoomstart", dropUncoveredFill);
  map.on("moveend", () => { dropUncoveredFill(); scheduleFill(); });

  map.on("click", (e) => {
    state.origin = { lat: e.latlng.lat, lon: e.latlng.lng };
    $("#origin-name").textContent = "Where you clicked";
    layers.origin.setLatLng(e.latlng);
    refresh();
  });

  // Walking already routes to the answer itself, so there is no last stretch to opt out
  // of. This used to grey the control rather than hide it, on the reasoning that a
  // control which comes and goes is harder to find than one that stays put. Charlie's
  // call, 06/09/2026: "the last stretch shouldnt even be shown as an option for
  // walking". A disabled checkbox still poses the question and still has to be read
  // before you can dismiss it.
  const walkleg = $("#walkleg");
  const syncWalkLeg = () => {
    const off = state.mode === "foot" || !PD;
    walkleg.disabled = off;
    const row = walkleg.closest("label");
    if (row) row.hidden = off;
  };

  document.querySelectorAll(".modes button").forEach((b) => {
    b.addEventListener("click", () => {
      state.mode = b.dataset.mode;
      document.querySelectorAll(".modes button").forEach((o) =>
        o.setAttribute("aria-pressed", String(o === b)));
      syncWalkLeg();
      refresh();
    });
  });

  walkleg.addEventListener("change", (e) => {
    state.walkLeg = e.target.checked;
    refresh();
  });
  syncWalkLeg();

  const mins = $("#mins");
  mins.addEventListener("input", () => {
    state.mins = +mins.value;
    const h = Math.floor(state.mins / 60), r = state.mins % 60;
    $("#mins-label").textContent = state.mins >= 60
      ? (h + " h" + (r ? " " + r + " min" : "")) : (state.mins + " min");
    refresh();
  });

  // refresh(), not render(): render alone paints the estimate and never asks for the
  // network, so the first view would silently show the fallback and draw no polygon.
  refresh();  // an answer is on screen before anyone touches a control
})();
