# Woop Woop

The emptiest place within reach.

Pick a starting point, a way of travelling and how long you have; get the spot in range
that is furthest from every road, building, railway, power line and runway - and how to
walk in to it.

Live at <https://charlietrenorden.com/woop-woop/>.

Isochrone tools are everywhere and they all draw the same thing: the area you can
reach. None of them rank what is *inside* it. The maps that do measure emptiness
(Germany's points-furthest-from-a-road, Britain's most remote point, Australia's) are
fixed national answers with no start point and no travel budget. This is the
intersection: your location, your transport, your time, and then the emptiest point in
range.

## How it works

    build/extract.py   pull roads, buildings, railways, power lines and runways out of a
                       Geofabrik .osm.pbf, plus coastline and water, over a buffered box
    build/raster.py    burn them into a 100 m grid, take a Euclidean distance transform,
                       mask the ocean, mark the inner box answerable
    build/peaks.py     keep only the LOCAL MAXIMA and pack them binary (640 KB for SEQ)
    build/icons.py     render the favicon files from favicon.svg
    docs/              the site: the query is a scan over the peak list

Rebuild:

    python build/extract.py data/australia-latest.osm.pbf
    python build/raster.py  data/seq-features.npz 100 data/seq-dist.npz
    python build/peaks.py

## Things that are easy to get wrong

* **Clipping ways to the box truncates them.** Keep whole ways if any vertex is inside.
  Clipping left a two-row gap in the coastline at the southern edge, the ocean flood
  escaped through it, and 98.6% of the region came back as sea.
* **Cells near the edge of the extract have inflated distances**, because the road
  nearest them was never loaded. Hence the 0.6 degree buffer, and answering only inside
  it.
* **Do not dilate the coastline barrier.** It seals narrow tidal channels and welds
  North Stradbroke and Russell Island to the mainland, which lets the app answer with
  somewhere you need a ferry to reach.
* **The naive maximum is usually degenerate** - an island, or open water. Reachability
  is a hard filter, not a display option.

## Why there is no tile pyramid

The obvious way to cover a continent is to cut the distance field into map tiles and
load the ones you need. It is also unnecessary. **The answer to "the furthest point in
this region" is always a local maximum of the field**, so the only cells that can ever
be an answer are the peaks. Australia at 100 m is 1.6 billion cells; its peaks, spaced
a kilometre apart and filtered to somewhere you can actually reach, are a list.

Each peak carries what the query needs - distance, how far off a track it sits, the
access point, and a land-component id so "can I get there without a boat" is an integer
comparison. The raster is build scaffolding and never leaves the machine.

## Building a continent

    python build/au.py                  # one stream over the .osm.pbf -> chunk buckets
    python build/au_build.py coarse     # 2 km land/ocean grid for the whole continent
    python build/au_build.py chunks     # per chunk: rasterise, EDTs, mine peaks
    python build/au_build.py land       # 500 m land mask, for clipping the drawn shape
    python build/au_build.py field      # CONTINENTAL 2 km distance fields - see below
    python build/water_areas.py         # assembled water polygons for the land mask
    python build/au_build.py merge      # correct, prune and pack into docs/data/
    python build/check_water.py <pbf> docs/data/peaks.json --drop   # independent sieve
    python build/verify_drive.py 6 main # independent check of the shipped distances

Australia: 79 non-empty chunks, 11.5M ways, 90.1M vertices, about an hour end to end.
873,751 raw peaks, 421,133 shipped after the water sieve; 82,813 raw drive-only peaks,
63,957 shipped. Over the wire, gzipped by GitHub Pages: peaks 3.5 MB, drive-only 787 KB,
the 500 m land mask 325 KB, the landmass grid 8 KB.

Six traps this hit, all of them silent:

* **A per-chunk distance transform CANNOT report a distance larger than its buffer.**
  It measures to the nearest feature the chunk LOADED, and where the true nearest sits
  outside the 0.6 degree (~66 km) buffer it quietly measures to something further away
  instead. This shipped: the headline read 176.4 km where an independent 300 km
  point-to-segment search finds 137.5 km, overstated by 28%, and the drive-only field
  was out by up to 54 km. Buildings and power lines are sparse enough in the desert
  that the blind spot covers exactly the country that produces the best answers.
  Fixed by `au_build.py field` - one continental 2 km grid, taken as a MINIMUM against
  the per-chunk value. The minimum is the right operator because a per-chunk EDT can
  only err upwards: it can miss a nearer feature, never invent one. Small city
  distances keep the 100 m grid's precision; remote ones get the correction.
  `build/verify_drive.py` is the check, and it must be run after any change here.
* **`EmptyTagFilter` discards the multipolygon rings `water_relation_ways` exists to
  find.** A relation's rings are UNTAGGED ways - the tags live on the relation - so the
  speed filter threw away precisely what the water pass had just gone looking for.
  Sydney Harbour is `natural=water` / `water=harbour` on a relation whose rings are 41
  untagged ways; its outline never assembled, and because OSM's coastline crosses the
  harbour mouth rather than tracing the shoreline, the ocean flood could not reach it
  either. The harbour came out as standable LAND, in the same connected region as the
  whole inland continent.

* **Seed the fine water flood from the OCEAN, never from `wet`.** Wet includes inland
  lakes, the fine labelling only splits on the coastline, so one lake cell drags the
  whole inland label under. Measured: 98.6% of a chunk came back as water, and 660
  peaks where there should have been 26,000.
* **Compute landmass components from the sea alone.** Including rivers made every
  capital its own landmass - Sydney answered 0.7 km whether you had one hour or four.
* **Component ids are ranked by size, so resolve an origin to the LOWEST id nearby**,
  not the nearest. Nearest put Brisbane on a sand island offshore.
* **Distances are stored in decametres.** A uint16 of metres tops out at 65.5 km and
  the best peak in Australia is over 150 km, so metres clipped the entire desert to one
  number.

## Two questions, two peak files

**Walk and ride** answer from `peaks.bin`, which maximises distance from ANYTHING -
roads included - so its answers sit at the end of fire trails and footpaths. Best in
Australia: 154.7 km.

**Drive, with the last stretch switched off**, answers from `peaks-drive.bin`. A point
on a road is 0 m from anything by the definition above, because a road IS civilisation,
so that file maximises a different field: the road is excluded from the MEASUREMENT but
still required underfoot. It is the emptiest place you can park. Best: 119.1 km. The
headline says "from anything but roads" in that mode, because it is a different claim.

Note the consequence of excluding roads: two highways crossing in the desert both score
well, since no road counts toward the distance. That follows from the definition rather
than being a fault in it.

## Status

Distances are checked by `build/verify_drive.py` against an independent 300 km
point-to-segment search, on an ASYMMETRIC criterion. Claiming MORE emptiness than exists
is the product lying; claiming less is caution. Currently the main field overstates
nothing at all and understates by at most 2.6 km, and the drive field's worst
overstatement is 1.1 km on a 113 km answer - inside what a 2 km correction grid can
explain. `build/check_water.py --drop` is the other guard: it assembles real water
multipolygons and removed 280 peaks that were sitting on open water.

**Reachability follows the real road network**, through openrouteservice, and the time
budget covers BOTH legs - the drive to the last built ground and the walk in from it -
using nested isochrone bands to bound the driven part. Only DRIVING is capped at an
hour; foot and bike run to the slider's limit. Beyond that cap, driving falls back to a
circle and says so.

Coverage is all of Australia.

Data: [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, via
[Geofabrik](https://download.geofabrik.de/).
