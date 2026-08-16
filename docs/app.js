/* Woop Woop - find the emptiest reachable point.
 *
 * The whole measurement runs in the browser. seq.png is one byte per 100 m cell
 * holding the distance from that cell to the nearest road, building, railway, power
 * line or runway, so answering a query is a scan over pixels rather than a request to
 * anything. 0 means you cannot stand there (ocean, lakes, outside the mapped region).
 */
const DATA = "data/";
const FLOOR = 10;   // in step_m units: draw nothing closer than 500 m to something

// Average progress, not top speed - a car does not hold 100 km/h on the way out of
// town. Marked as an estimate in the UI because it IS one: the real version asks a
// routing engine which roads exist and how fast they are.
const MODES = {
  foot: { label: "Walk", verb: "walk", kmh: 4.5, detour: 0.80 },
  bike: { label: "Ride", verb: "ride", kmh: 15.0, detour: 0.75 },
  car: { label: "Drive", verb: "drive", kmh: 70.0, detour: 0.70 },
};

const state = {
  mode: "car", mins: 60,
  origin: { lat: -27.4698, lon: 153.0251 }, originName: "Brisbane",
};
let meta, grid, map, layers = {};

const $ = (s) => document.querySelector(s);
const fmtKm = (m) => (m < 950 ? Math.round(m) + " m" : (m / 1000).toFixed(1) + " km");

/* ---------- grid helpers: the same equirectangular mapping the build used ---------- */
const toPx = (lat, lon) => [
  ((lon - meta.west) / (meta.east - meta.west)) * meta.width,
  ((meta.north - lat) / (meta.north - meta.south)) * meta.height,
];
const toLL = (x, y) => [
  meta.north - (y / meta.height) * (meta.north - meta.south),
  meta.west + (x / meta.width) * (meta.east - meta.west),
];
const at = (x, y) =>
  (x < 0 || y < 0 || x >= meta.width || y >= meta.height)
    ? 0 : grid[y * meta.width + x];

async function load() {
  meta = await (await fetch(DATA + "seq.json")).json();
  const img = new Image();
  img.src = DATA + "seq.png";
  await img.decode();
  const c = document.createElement("canvas");
  c.width = meta.width; c.height = meta.height;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  const rgba = ctx.getImageData(0, 0, meta.width, meta.height).data;
  grid = new Uint8Array(meta.width * meta.height);
  for (let i = 0; i < grid.length; i++) grid[i] = rgba[i * 4];  // greyscale: R only
  return overlayURL();
}

/* Colour the field so the map shows WHERE the empty country is, not just one pin.
 *
 * Drawn at a FRACTION of the data resolution. The grid is 1977x3112, and handing
 * Leaflet a 6-megapixel image means the browser recomposites all of it on every pan
 * and zoom frame, which is what made the map feel heavy. The full-resolution grid is
 * still what gets measured - this is only what gets looked at.
 *
 * Downsampling takes the MAXIMUM of each block, not the average. A remote spot is a
 * few bright cells surrounded by dark ones, so averaging is exactly the operation that
 * would erase the thing the map exists to show.
 */
const OVERLAY_MAX_PX = 900;

function overlayURL() {
  const f = Math.max(1, Math.ceil(Math.max(meta.width, meta.height) / OVERLAY_MAX_PX));
  const W = Math.ceil(meta.width / f), H = Math.ceil(meta.height / f);
  const small = new Uint8Array(W * H);
  for (let y = 0; y < meta.height; y++) {
    const oy = ((y / f) | 0) * W;
    const row = y * meta.width;
    for (let x = 0; x < meta.width; x++) {
      const v = grid[row + x];
      const k = oy + ((x / f) | 0);
      if (v > small[k]) small[k] = v;
    }
  }

  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const ctx = c.getContext("2d");
  const im = ctx.createImageData(W, H);
  const top = Math.max(1, meta.max_m / meta.step_m);
  for (let i = 0; i < small.length; i++) {
    const v = small[i];
    const j = i * 4;
    if (v === 0) { im.data[j + 3] = 0; continue; }
    // Nothing under FLOOR is drawn at all. A ramp that starts at zero tints the
    // entire city a dark blue-green that is indistinguishable from the basemap's own
    // parkland, so the map ends up looking untouched. Starting at 500 m means the
    // colour only ever means "this is the empty part".
    if (v < FLOOR) { im.data[j + 3] = 0; continue; }
    const t = Math.sqrt((v - FLOOR) / Math.max(1, top - FLOOR));
    im.data[j] = 90 + 165 * t;
    im.data[j + 1] = 40 + 150 * t;
    im.data[j + 2] = 60 + 40 * t;
    im.data[j + 3] = 45 + 165 * t;
  }
  ctx.putImageData(im, 0, 0);
  return c.toDataURL("image/png");
}

/* Snap a clicked point to the nearest cell you could actually stand on.
 * Clicking in the middle of the bay is a reasonable thing for someone to do. */
function snapToLand(x, y) {
  if (at(x, y) > 0) return [x, y];
  for (let r = 1; r < 120; r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        if (at(x + dx, y + dy) > 0) return [x + dx, y + dy];
      }
    }
  }
  return null;
}

/* Which cells can you get to WITHOUT crossing water.
 *
 * This is the hard filter, not a nicety. Without it the answer for Brisbane is a
 * sand island in Moreton Bay that you would need a boat to reach - the same
 * degenerate result the German prior art had to work around by hand. A straight-line
 * range says the island is 25 km away and therefore fine; land connectivity says you
 * cannot walk or drive there at all.
 *
 * A flood fill is not a substitute for a real road-network isochrone. It is the part
 * of the answer that a routing engine would also enforce, done now so the result is
 * honest in the meantime.
 */
function landReach(sx, sy, x0, y0, x1, y1) {
  const W = meta.width;
  const seen = new Uint8Array((x1 - x0 + 1) * (y1 - y0 + 1));
  const idx = (x, y) => (y - y0) * (x1 - x0 + 1) + (x - x0);
  const q = new Int32Array((x1 - x0 + 1) * (y1 - y0 + 1) * 2);
  let head = 0, tail = 0;
  q[tail++] = sx; q[tail++] = sy; seen[idx(sx, sy)] = 1;
  while (head < tail) {
    const x = q[head++], y = q[head++];
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const nx = x + dx, ny = y + dy;
        if (nx < x0 || ny < y0 || nx > x1 || ny > y1) continue;
        const i = idx(nx, ny);
        if (seen[i] || grid[ny * W + nx] === 0) continue;
        seen[i] = 1; q[tail++] = nx; q[tail++] = ny;
      }
    }
  }
  return { seen: seen, idx: idx };
}

/* ---------- the query ---------- */
function solve() {
  const m = MODES[state.mode];
  const reachM = (m.kmh * 1000) * (state.mins / 60) * m.detour;

  const mPerDegLat = 111320;
  const mPerDegLon = mPerDegLat * Math.cos((state.origin.lat * Math.PI) / 180);
  const p = toPx(state.origin.lat, state.origin.lon);
  const rx = Math.ceil((reachM / mPerDegLon) / ((meta.east - meta.west) / meta.width));
  const ry = Math.ceil((reachM / mPerDegLat) / ((meta.north - meta.south) / meta.height));
  const ox = p[0], oy = p[1];

  const x0 = Math.max(0, Math.floor(ox - rx));
  const x1 = Math.min(meta.width - 1, Math.ceil(ox + rx));
  const y0 = Math.max(0, Math.floor(oy - ry));
  const y1 = Math.min(meta.height - 1, Math.ceil(oy + ry));

  const start = snapToLand(Math.round(ox), Math.round(oy));
  if (!start) return null;
  const land = landReach(start[0], start[1], x0, y0, x1, y1);

  let best = 0, bx = -1, by = -1;
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      const v = grid[y * meta.width + x];
      if (v <= best) continue;                      // cheapest test first
      if (!land.seen[land.idx(x, y)]) continue;     // no boats
      const ll = toLL(x + 0.5, y + 0.5);
      const dx = (ll[1] - state.origin.lon) * mPerDegLon;
      const dy = (ll[0] - state.origin.lat) * mPerDegLat;
      if (dx * dx + dy * dy > reachM * reachM) continue;
      best = v; bx = x; by = y;
    }
  }
  if (bx < 0) return null;

  const ll = toLL(bx + 0.5, by + 0.5);
  return {
    lat: ll[0], lon: ll[1], dist_m: best * meta.step_m,
    reachM: reachM, access: walkOut(bx, by),
  };
}

/* Follow the distance field downhill to find the way in.
 *
 * The value at a cell is its distance to the nearest anything, so stepping to the
 * lowest neighbour repeatedly arrives at that nearest thing - which is the road or
 * track you would park on. It needs no extra data shipped to the browser.
 */
function walkOut(x, y) {
  const path = [[x, y]];
  for (let i = 0; i < 4000; i++) {
    let bv = at(x, y), nx = x, ny = y;
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const v = at(x + dx, y + dy);
        if (v > 0 && v < bv) { bv = v; nx = x + dx; ny = y + dy; }
      }
    }
    if (nx === x && ny === y) break;
    x = nx; y = ny; path.push([x, y]);
    if (bv <= 1) break;
  }
  const ll = toLL(x + 0.5, y + 0.5);
  return {
    lat: ll[0], lon: ll[1],
    path: path.map((p) => toLL(p[0] + 0.5, p[1] + 0.5)),
  };
}

/* ---------- rendering ---------- */
function render() {
  const a = solve();
  const box = $("#answer");
  $("#origin-ll").textContent =
    state.origin.lat.toFixed(3) + ", " + state.origin.lon.toFixed(3);

  ["target", "access", "line", "reach"].forEach((k) => {
    if (layers[k]) { map.removeLayer(layers[k]); layers[k] = null; }
  });
  if (!a) {
    box.className = "empty";
    box.textContent = "Nothing in range yet. South East Queensland is mapped "
      + "first; the rest is being added.";
    return;
  }

  const m = MODES[state.mode];
  box.className = "";
  box.innerHTML =
    '<div class="big">' + fmtKm(a.dist_m) + " <span>from anything</span></div>" +
    '<ul class="leg">' +
    "<li><b>" + a.lat.toFixed(4) + ", " + a.lon.toFixed(4) + "</b></li>" +
    "<li>" + m.verb.charAt(0).toUpperCase() + m.verb.slice(1) + " to <b>" +
      a.access.lat.toFixed(4) + ", " + a.access.lon.toFixed(4) +
      "</b>, the last built ground on the way.</li>" +
    "<li>Then walk the final <b>" + fmtKm(a.dist_m) + "</b>.</li>" +
    '<li><a target="_blank" rel="noopener" href="https://www.openstreetmap.org/#map=14/' +
      a.lat.toFixed(4) + "/" + a.lon.toFixed(4) + '">See it on OpenStreetMap</a></li>' +
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
  const url = await load();

  map = L.map("map", { zoomControl: true })
    .setView([state.origin.lat, state.origin.lon], 8);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 17,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">' +
      "OpenStreetMap</a> contributors",
  }).addTo(map);
  L.imageOverlay(url, [[meta.south, meta.west], [meta.north, meta.east]],
    { opacity: 0.85, interactive: false }).addTo(map);
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
