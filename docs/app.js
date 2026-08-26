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

const state = {
  mode: "foot", mins: 60,
  origin: { lat: -27.4698, lon: 153.0251 },
  exact: true,           // use the real road-network isochrone
  iso: null,             // its rings, once fetched
  isoNote: "",           // why it is not being used, if it is not
};

// The Worker holds the openrouteservice key. The page never sees it.
const ISO_URL = "https://woop-woop-iso.charlie-tren.workers.dev";

// Keyed the same way the Worker rounds - about 110 m - so nudging the map or the
// slider back to somewhere already asked about costs nothing. The free plan allows
// 500 isochrones a DAY, and a slider dragged across its range would spend fifty.
const isoCache = new Map();

// Measured, not read in a doc: openrouteservice refuses range > 3600 s with error 3004
// ("Maximum possible value is 3600"). The time slider goes to four hours, so above an
// hour the real-roads check simply cannot answer and the page has to say so rather
// than quietly showing the estimate as though it were exact.
const ISO_MAX_MINUTES = 60;
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
let meta, P, comp, map, layers = {};

const $ = (s) => document.querySelector(s);
const fmtKm = (m) => (m < 950 ? Math.round(m) + " m" : (m / 1000).toFixed(1) + " km");

async function load() {
  meta = await (await fetch(DATA + "peaks.json")).json();
  const buf = await (await fetch(DATA + "peaks.bin")).arrayBuffer();
  const n = meta.count;
  let o = 0;
  const take = (Type, count) => {
    const a = new Type(buf, o, count); o += count * Type.BYTES_PER_ELEMENT; return a;
  };
  // Structure of arrays, in the order the build wrote them.
  const lat = take(Int32Array, n), lon = take(Int32Array, n);
  const d = take(Uint16Array, n), off = take(Uint16Array, n);
  const alat = take(Int32Array, n), alon = take(Int32Array, n);
  const c = take(Uint16Array, n);
  P = { n: n, s: meta.coord_scale, ds: meta.dist_scale_m || 1,
        lat: lat, lon: lon, d: d, off: off, alat: alat, alon: alon, c: c };

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
  const reachM = (m.kmh * 1000) * (state.mins / 60) * m.detour;
  const mPerDegLat = 111320;
  const mPerDegLon = mPerDegLat * Math.cos((state.origin.lat * Math.PI) / 180);
  const want = componentNear(state.origin.lat, state.origin.lon);
  if (!want) return null;

  // With a real isochrone, reachability is containment in the polygon rather than a
  // radius - which matters more than it sounds. The measured shape is 2 to 7.5 times
  // less circular than a disc, so the two tests disagree constantly: the circle both
  // includes country with no road to it and excludes places an hour up a highway.
  const rings = state.exact ? state.iso : null;
  const bb = rings ? ringBounds(rings[0]) : null;

  let nearest = null, nearestM = Infinity;
  for (let i = 0; i < P.n; i++) {
    if (P.c[i] !== want) continue;
    const lat = P.lat[i] / P.s, lon = P.lon[i] / P.s;
    const dx = (lon - state.origin.lon) * mPerDegLon;
    const dy = (lat - state.origin.lat) * mPerDegLat;
    const away = Math.sqrt(dx * dx + dy * dy);

    let reachable;
    if (rings) {
      // Bounding box first: the polygon test is the expensive one and most peaks are
      // nowhere near it.
      reachable = lat >= bb.s && lat <= bb.n && lon >= bb.w && lon <= bb.e &&
                  inIsochrone(lat, lon, rings);
    } else {
      reachable = away <= reachM;
    }

    if (reachable) {
      return {
        lat: lat, lon: lon, dist_m: P.d[i] * P.ds, offtrack_m: P.off[i] * P.ds,
        reachM: reachM, awayM: away, overBudget: false, exact: !!rings,
        access: { lat: P.alat[i] / P.s, lon: P.alon[i] / P.s },
      };
    }
    if (away < nearestM) {
      nearestM = away;
      nearest = {
        lat: lat, lon: lon, dist_m: P.d[i] * P.ds, offtrack_m: P.off[i] * P.ds,
        reachM: reachM, awayM: away, overBudget: true, exact: !!rings,
        access: { lat: P.alat[i] / P.s, lon: P.alon[i] / P.s },
      };
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

  for (const k of ["target", "iso"]) {
    if (layers[k]) { map.removeLayer(layers[k]); layers[k] = null; }
  }
  if (state.exact && state.iso) {
    // Leaflet wants [lat, lon]; GeoJSON is [lon, lat].
    layers.iso = L.polygon(
      state.iso.map((r) => r.map((p) => [p[1], p[0]])),
      { color: "#e2674a", weight: 2, opacity: 1, fillOpacity: 0.22,
        fillColor: "#c2451f", interactive: false }).addTo(map);
  }
  if (!a) {
    box.className = "empty";
    box.textContent = "Nothing in range. Try more time, or a start point in "
      + "Australia - that is the extent of the map so far.";
    return;
  }

  const m = MODES[state.mode];
  // Every spot now sits ON a road, track or footpath, so the second link can ask for
  // directions to the spot itself rather than only to the last drivable point.
  const gwalk = "https://www.google.com/maps/dir/?api=1&travelmode=walking&origin=" +
    a.access.lat.toFixed(5) + "," + a.access.lon.toFixed(5) +
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
      fmtMins(minutesFor(m, a.awayM)) + "</b> away in a straight line.</p>"
    : "";
  const note = state.isoNote
    ? '<p class="over">' + state.isoNote + "</p>"
    : "";
  box.innerHTML = note + over +
    '<div class="big">' + fmtKm(a.dist_m) + " <span>from anything</span></div>" +
    '<ul class="leg">' +
    "<li><b>" + a.lat.toFixed(4) + ", " + a.lon.toFixed(4) + "</b></li>" +
    "<li>" + m.verb.charAt(0).toUpperCase() + m.verb.slice(1) + " to <b>" +
      a.access.lat.toFixed(4) + ", " + a.access.lon.toFixed(4) +
      "</b>, the last built ground on the way.</li>" +
    "<li>Then <b>" + fmtKm(a.dist_m) + "</b> along tracks. The spot is ON a track, " +
      "so you can follow it the whole way.</li>" +
    '<li><a target="_blank" rel="noopener" href="' + gmaps + '">Directions to the ' +
      'drop-off</a> &middot; <a target="_blank" rel="noopener" href="' + gwalk +
      '">walking directions to the spot</a></li>' +
    "</ul>";

  layers.target = L.circleMarker([a.lat, a.lon], {
    radius: 7, color: "#fff", weight: 2, fillColor: "#e2674a", fillOpacity: 1,
  }).addTo(map).bindTooltip("Furthest from anything");

  const view = L.latLngBounds([[a.lat, a.lon],
                               [state.origin.lat, state.origin.lon]]);
  if (layers.iso) view.extend(layers.iso.getBounds());
  map.fitBounds(view.pad(0.12), { animate: false });
}

/* Redraw immediately from the estimate, then fetch the real isochrone if asked.
 *
 * The API call is debounced and the drawing is not, so dragging the slider stays
 * responsive and spends at most one call per pause. 20 requests a minute is the plan's
 * limit and a dragged slider would fire hundreds.
 */
let isoTimer = null, isoSeq = 0;

function refresh() {
  render();
  if (!state.exact) return;
  clearTimeout(isoTimer);
  if (state.mins > ISO_MAX_MINUTES) {
    state.iso = null;
    state.isoNote = "Real roads only go up to an hour - openrouteservice will not "
      + "compute a longer one. This is the estimate.";
    render();
    return;
  }
  const seq = ++isoSeq;
  state.isoNote = "Checking the real roads…";
  render();
  isoTimer = setTimeout(async () => {
    const origin = { lat: state.origin.lat, lon: state.origin.lon };
    const mode = state.mode, mins = state.mins;
    try {
      const rings = await fetchIsochrone(origin, mode, mins);
      if (seq !== isoSeq) return;        // a newer request has overtaken this one
      state.iso = rings;
      state.isoNote = "";
    } catch (err) {
      if (seq !== isoSeq) return;
      state.iso = null;
      state.isoNote = err.message === "quota"
        ? "The routing quota for today is gone, so this is the estimate again."
        : "No route could be worked out from here, so this is the estimate.";
    }
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

  document.querySelectorAll(".modes button").forEach((b) => {
    b.addEventListener("click", () => {
      state.mode = b.dataset.mode;
      document.querySelectorAll(".modes button").forEach((o) =>
        o.setAttribute("aria-pressed", String(o === b)));
      refresh();
    });
  });

  const mins = $("#mins");
  mins.addEventListener("input", () => {
    state.mins = +mins.value;
    const h = Math.floor(state.mins / 60), r = state.mins % 60;
    $("#mins-label").textContent = state.mins >= 60
      ? (h + " h" + (r ? " " + r + " min" : "")) : (state.mins + " min");
    refresh();
  });

  $("#exact").addEventListener("change", (e) => {
    state.exact = e.target.checked;
    if (!state.exact) { state.iso = null; state.isoNote = ""; }
    refresh();
  });

  // refresh(), not render(): render alone paints the estimate and never asks for the
  // isochrone, so with real roads on by default the first view silently showed the
  // fallback and drew no polygon.
  refresh();  // an answer is on screen before anyone touches a control
})();
