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
// maxOff is how far off a road or track the spot may sit, and it depends on how you
// got there. Three hundred metres of scrub is nothing at the end of a day's drive and
// completely unreasonable at the end of an hour's walk in a city, which is why walking
// answers kept landing in the middle of a forest with no way in.
const MODES = {
  foot: { label: "Walk", verb: "walk", kmh: 4.5, detour: 0.80, maxOff: 150 },
  bike: { label: "Ride", verb: "ride", kmh: 15.0, detour: 0.75, maxOff: 150 },
  car: { label: "Drive", verb: "drive", kmh: 70.0, detour: 0.70, maxOff: 300 },
};

const state = {
  mode: "car", mins: 60,
  origin: { lat: -27.4698, lon: 153.0251 },
};
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

  // Peaks are sorted furthest-from-anything first, so the FIRST one in range is the
  // answer. While scanning, also remember the nearest peak that qualifies but is out
  // of range - so that when nothing is reachable there is still something to show.
  //
  // Without that fallback the page just said "nothing in range", which was true and
  // useless: 84% of hour-long WALKS from a capital hit it, because an hour on foot
  // genuinely does not reach anywhere empty. Better to show the nearest real answer
  // and say honestly how long it would take.
  let nearest = null, nearestM = Infinity;
  for (let i = 0; i < P.n; i++) {
    if (P.c[i] !== want) continue;
    if (P.off[i] * P.ds > m.maxOff) continue;
    const lat = P.lat[i] / P.s, lon = P.lon[i] / P.s;
    const dx = (lon - state.origin.lon) * mPerDegLon;
    const dy = (lat - state.origin.lat) * mPerDegLat;
    const away = Math.sqrt(dx * dx + dy * dy);
    if (away <= reachM) {
      return {
        lat: lat, lon: lon, dist_m: P.d[i] * P.ds, offtrack_m: P.off[i] * P.ds,
        reachM: reachM, awayM: away, overBudget: false,
        access: { lat: P.alat[i] / P.s, lon: P.alon[i] / P.s },
      };
    }
    if (away < nearestM) {
      nearestM = away;
      nearest = {
        lat: lat, lon: lon, dist_m: P.d[i] * P.ds, offtrack_m: P.off[i] * P.ds,
        reachM: reachM, awayM: away, overBudget: true,
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

  if (layers.target) { map.removeLayer(layers.target); layers.target = null; }
  if (!a) {
    box.className = "empty";
    box.textContent = "Nothing in range. Try more time, or a start point in "
      + "Australia - that is the extent of the map so far.";
    return;
  }

  const m = MODES[state.mode];
  const gmaps = "https://www.google.com/maps/dir/?api=1&origin=" +
    state.origin.lat.toFixed(5) + "," + state.origin.lon.toFixed(5) +
    "&destination=" + a.access.lat.toFixed(5) + "," + a.access.lon.toFixed(5) +
    "&travelmode=" + (state.mode === "car" ? "driving"
      : state.mode === "bike" ? "bicycling" : "walking");

  box.className = "";
  const over = a.overBudget
    ? '<p class="over">Nothing empty is within ' + fmtMins(state.mins) + " " +
      m.verb + " of here. The nearest is <b>" +
      fmtMins(minutesFor(m, a.awayM)) + "</b> away.</p>"
    : "";
  box.innerHTML = over +
    '<div class="big">' + fmtKm(a.dist_m) + " <span>from anything</span></div>" +
    '<ul class="leg">' +
    "<li><b>" + a.lat.toFixed(4) + ", " + a.lon.toFixed(4) + "</b></li>" +
    "<li>" + m.verb.charAt(0).toUpperCase() + m.verb.slice(1) + " to <b>" +
      a.access.lat.toFixed(4) + ", " + a.access.lon.toFixed(4) +
      "</b>, the last built ground on the way.</li>" +
    "<li>From there it is <b>" + fmtKm(a.dist_m) + "</b> further out - tracks " +
      "most of the way, the last <b>" + fmtKm(a.offtrack_m) + "</b> off them.</li>" +
    '<li><a target="_blank" rel="noopener" href="' + gmaps + '">Directions to the ' +
      'drop-off</a> &middot; <a target="_blank" rel="noopener" ' +
      'href="https://www.google.com/maps/search/?api=1&query=' +
      a.lat.toFixed(5) + "," + a.lon.toFixed(5) + '">the spot itself</a></li>' +
    "</ul>";

  layers.target = L.circleMarker([a.lat, a.lon], {
    radius: 7, color: "#fff", weight: 2, fillColor: "#e2674a", fillOpacity: 1,
  }).addTo(map).bindTooltip("Furthest from anything");

  map.fitBounds(
    L.latLngBounds([[a.lat, a.lon], [state.origin.lat, state.origin.lon]]).pad(0.35),
    { animate: false });
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
    render();
  });

  document.querySelectorAll(".modes button").forEach((b) => {
    b.addEventListener("click", () => {
      state.mode = b.dataset.mode;
      document.querySelectorAll(".modes button").forEach((o) =>
        o.setAttribute("aria-pressed", String(o === b)));
      render();
    });
  });

  const mins = $("#mins");
  mins.addEventListener("input", () => {
    state.mins = +mins.value;
    const h = Math.floor(state.mins / 60), r = state.mins % 60;
    $("#mins-label").textContent = state.mins >= 60
      ? (h + " h" + (r ? " " + r + " min" : "")) : (state.mins + " min");
    render();
  });

  render();   // an answer is on screen before anyone touches a control
})();
