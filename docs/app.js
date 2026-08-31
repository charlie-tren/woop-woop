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
const ISO_MAX_MINUTES = { foot: 240, bike: 240, car: 60 };

const state = {
  mode: "foot", mins: 60,
  // Sydney, not Brisbane. Changed 31/08/2026 - the homepage card is a picture of
  // whatever this opens on, and the site is written from Sydney.
  origin: { lat: -33.8688, lon: 151.2093 },
  walkLeg: true,         // willing to walk the last stretch off the road network
  bands: null,           // [{mins, rings}] innermost first, once fetched
  isoNote: "",           // why the real network is not being used, if it is not
  busy: false,
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
    const err = new Error(res.status === 429 ? "quota" : "route");
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

/* ---------- the land mask ---------- */
/* One bit per square kilometre of the continent, shipped as a 1-bit PNG.
 *
 * It exists because the isochrone is a generalised hull: where two reachable shores
 * face each other it spans the water between them, and the fill was being painted over
 * Sydney Harbour. The component grid already shipped is 4 km, which cannot resolve a
 * harbour one to three kilometres wide - masking with that would eat real foreshore.
 *
 * Kept BIT-PACKED. Decoded off a canvas once and then thrown away: the continent is
 * 16.6M cells, which is 2.1 MB of bits and 66 MB of RGBA.
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

  const img = new Image();
  img.src = DATA + g.file;
  await img.decode();
  const c = document.createElement("canvas");
  c.width = g.width; c.height = g.height;
  const cx = c.getContext("2d", { willReadFrequently: true });
  cx.drawImage(img, 0, 0);
  const px = cx.getImageData(0, 0, g.width, g.height).data;
  const n = g.width * g.height;
  const bits = new Uint8Array((n + 7) >> 3);
  for (let i = 0; i < n; i++) {
    if (px[i * 4] > 127) bits[i >> 3] |= 1 << (i & 7);
  }
  landBits = bits;
  c.width = c.height = 0;
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
      if (landAt(lat, lon)) {
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

/* ---------- the query ---------- */
function solve() {
  const m = MODES[state.mode];
  const mPerDegLat = 111320;
  const mPerDegLon = mPerDegLat * Math.cos((state.origin.lat * Math.PI) / 180);
  const want = componentNear(state.origin.lat, state.origin.lon);
  if (!want) return null;

  const bands = state.bands;
  const toSpot = walksToSpot();
  const Q = activeSet();

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
    if (reachable) return hit;
    if (away < nearestM) {
      nearestM = away;
      nearest = Object.assign(hit, { overBudget: true });
    }
  }
  return nearest;
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
  const a = solve();
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
      const seq = ++fillSeq;
      paintLandFill(outer.rings, (bounds) => {
        if (seq !== fillSeq || !bounds || !fillURL) return;
        if (layers.fill) map.removeLayer(layers.fill);
        layers.fill = L.imageOverlay(fillURL, bounds,
          { opacity: 1, interactive: false, className: "iso-fill" }).addTo(map);
      });
    }
  }
  if (!a) {
    box.className = "empty";
    box.textContent = "Nothing in range. Try more time, or a start point in "
      + "Australia - that is the extent of the map so far.";
    return;
  }

  const m = MODES[state.mode];
  const wfrom = walksToSpot() ? state.origin : a.access;
  const gwalk = "https://www.google.com/maps/dir/?api=1&travelmode=walking&origin=" +
    wfrom.lat.toFixed(5) + "," + wfrom.lon.toFixed(5) +
    "&destination=" + a.lat.toFixed(5) + "," + a.lon.toFixed(5);
  const gmaps = "https://www.google.com/maps/dir/?api=1&origin=" +
    state.origin.lat.toFixed(5) + "," + state.origin.lon.toFixed(5) +
    "&destination=" + a.access.lat.toFixed(5) + "," + a.access.lon.toFixed(5) +
    "&travelmode=" + (state.mode === "car" ? "driving"
      : state.mode === "bike" ? "bicycling" : "walking");

  box.className = "";
  const over = a.overBudget
    ? '<p class="over">Nothing in range within ' + fmtMins(state.mins) + " " +
      m.verb + " of here. The nearest is <b>" +
      fmtKm(a.awayM) + "</b> away in a straight line.</p>"
    : "";
  const note = state.isoNote ? '<p class="over">' + state.isoNote + "</p>" : "";

  // The trip, leg by leg. Walking routes to the spot itself; the wheeled modes stop
  // at the last built ground, and the rest is on foot whatever you came in.
  const legs = [];
  if (walksToSpot()) {
    legs.push("<li>" + (a.travelMins === null
      ? "Walk to <b>" + a.lat.toFixed(4) + ", " + a.lon.toFixed(4) + "</b>."
      : "Walk there in under <b>" + fmtMins(a.travelMins) + "</b>.") + "</li>");
  } else {
    const verb = a.travelMins === null
      ? m.verb.charAt(0).toUpperCase() + m.verb.slice(1)
      : "Under <b>" + fmtMins(a.travelMins) + "</b> " + m.verb;
    legs.push("<li>" + verb + " to <b>" +
      a.access.lat.toFixed(4) + ", " + a.access.lon.toFixed(4) +
      "</b>, the last built ground.</li>");
    legs.push("<li>Then <b>" + fmtKm(a.dist_m) + "</b> on foot along the track, " +
      "about <b>" + fmtMins(a.walkMins) + "</b>.</li>");
  }

  box.innerHTML = note + over +
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
        : "No route could be worked out from here, so this is an estimate.";
    }
    setBusy(false);
    render();
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

  map.on("click", (e) => {
    state.origin = { lat: e.latlng.lat, lon: e.latlng.lng };
    $("#origin-name").textContent = "Where you clicked";
    layers.origin.setLatLng(e.latlng);
    refresh();
  });

  // Walking already routes to the answer itself, so there is no last stretch to opt
  // out of. Disabled rather than hidden: a control that appears and disappears with
  // the mode is harder to find than one that greys.
  const walkleg = $("#walkleg");
  const syncWalkLeg = () => {
    walkleg.disabled = state.mode === "foot" || !PD;
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
