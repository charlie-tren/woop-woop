/* Isochrone proxy for Woop Woop.
 *
 * The page cannot call openrouteservice directly, because doing so puts the API key in
 * public JavaScript where anyone can read it - and the free plan allows 500 isochrones
 * a DAY, which is roughly ten minutes of somebody else's curiosity. The key lives here
 * as a Worker secret and never reaches the browser.
 *
 * Deploy:  npx wrangler deploy
 * Secret:  npx wrangler secret put ORS_KEY      (run this yourself; it is never logged)
 */

// The dashboard announces api.heigit.org as the replacement for
// api.openrouteservice.org, but measured on 19/08/2026 the new host returns an nginx
// 404 on both /v2/isochrones/... and /ors/v2/..., while the old one answers 200. The
// notice is forward-looking. Re-test before switching.
const UPSTREAM = "https://api.openrouteservice.org/v2/isochrones";

const PROFILES = {
  foot: "foot-walking",
  bike: "cycling-regular",
  car: "driving-car",
};

// Only these origins may call this Worker. Without it the key is still hidden but the
// quota is not - anyone could point their own page at this endpoint and spend it.
const ALLOWED = new Set([
  "https://charlietrenorden.com",
  "http://localhost:8731",
  "http://127.0.0.1:8731",
]);

// Coordinates are rounded before both the cache key AND the upstream call. Two clicks
// 50 m apart produce the same isochrone to any accuracy that matters, and rounding is
// what makes the cache actually hit: without it, every pixel of map is a fresh call.
const COORD_DP = 3;                       // ~110 m
const MAX_SECONDS = 4 * 3600;
// Bump whenever the upstream request body changes, to retire cached geometry.
const SHAPE = "s0";

function cors(origin) {
  const allow = ALLOWED.has(origin) ? origin : "https://charlietrenorden.com";
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

function bad(msg, status, origin) {
  return new Response(JSON.stringify({ error: msg }), {
    status: status,
    headers: { "Content-Type": "application/json", ...cors(origin) },
  });
}

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get("Origin") || "";
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(origin) });
    }
    if (request.method !== "POST") {
      return bad("POST only", 405, origin);
    }
    // An ABSENT Origin is rejected too, not just a wrong one. The page is on
    // charlietrenorden.com and this Worker is on workers.dev, so every real request is
    // cross-origin and browsers always send the header; anything without one is a
    // script, and a script can drain 500 isochrones in a minute. Measured before this
    // check went in: a plain curl with no Origin got a 200.
    if (!ALLOWED.has(origin)) {
      return bad("origin not allowed", 403, origin);
    }
    if (!env.ORS_KEY) {
      return bad("the worker has no ORS_KEY secret set", 500, origin);
    }

    let body;
    try {
      body = await request.json();
    } catch (e) {
      return bad("body must be JSON", 400, origin);
    }

    const profile = PROFILES[body.mode];
    const lat = Number(body.lat), lon = Number(body.lon);
    const seconds = Math.round(Number(body.seconds));
    if (!profile) return bad("mode must be foot, bike or car", 400, origin);
    if (!isFinite(lat) || lat < -90 || lat > 90) return bad("bad lat", 400, origin);
    if (!isFinite(lon) || lon < -180 || lon > 180) return bad("bad lon", 400, origin);
    if (!isFinite(seconds) || seconds < 60 || seconds > MAX_SECONDS) {
      return bad("seconds must be 60..14400", 400, origin);
    }

    const rlat = lat.toFixed(COORD_DP), rlon = lon.toFixed(COORD_DP);
    // The key carries SHAPE, so changing the request below actually changes what comes
    // back. Without it a change to the request below is invisible for a day:
    // the edge keeps serving the geometry the old settings produced.
    const cacheKey = new Request(
      `https://woop-woop.invalid/iso/${SHAPE}/${body.mode}/${seconds}/${rlat}/${rlon}`,
      { method: "GET" });
    const cache = caches.default;

    const hit = await cache.match(cacheKey);
    if (hit) {
      const out = new Response(hit.body, hit);
      out.headers.set("X-Woop-Cache", "hit");
      for (const [k, v] of Object.entries(cors(origin))) out.headers.set(k, v);
      return out;
    }

    // openrouteservice takes [lon, lat] - longitude FIRST. The reverse is the single
    // most common way to get an isochrone for the wrong hemisphere.
    const ask = {
      locations: [[Number(rlon), Number(rlat)]],
      range: [seconds],
      range_type: "time",
      // Zero smoothing, because the polygon is now DRAWN as well as tested. Smoothing
      // generalises the hull outwards from the road network, and where two reachable
      // shores face each other it spans the gap between them - which is water.
      //
      // Measured on the Brisbane 60 min drive at the previous smoothing of 15: the
      // shape crossed Bramble Bay, about 6 km offshore at the worst point, between
      // Redcliffe and the northern suburbs. That is the visible artefact, and it is
      // what this line targets.
      //
      // Ferries were NOT a cause, contrary to the first version of this comment.
      // Tested every ferry-only island around Brisbane - Dunwich and Point Lookout on
      // North Stradbroke, Russell, Macleay - and the isochrone contained none of them,
      // so avoiding them bought nothing measurable. Not kept as insurance either: the
      // page hands the user Google Maps DRIVING directions, and those DO route over
      // ferries, so suppressing them here would make the reachable set disagree with
      // the directions offered for reaching it.
      smoothing: 0,
    };

    const upstream = await fetch(`${UPSTREAM}/${profile}`, {
      method: "POST",
      headers: {
        "Authorization": env.ORS_KEY,
        "Content-Type": "application/json",
        "Accept": "application/geo+json",
      },
      body: JSON.stringify(ask),
    });

    const text = await upstream.text();
    if (!upstream.ok) {
      // Pass the status through rather than flattening it: 429 means the daily quota
      // is gone, and the page should say that rather than "something went wrong".
      return new Response(
        JSON.stringify({ error: "upstream", status: upstream.status,
                         detail: text.slice(0, 300) }),
        { status: upstream.status === 429 ? 429 : 502,
          headers: { "Content-Type": "application/json", ...cors(origin) } });
    }

    const res = new Response(text, {
      headers: {
        "Content-Type": "application/geo+json",
        // A day in the edge cache. Roads do not move, and the quota is the scarce
        // resource here, not freshness.
        "Cache-Control": "public, max-age=86400",
        "X-Woop-Cache": "miss",
        ...cors(origin),
      },
    });
    ctx.waitUntil(cache.put(cacheKey, res.clone()));
    return res;
  },
};
