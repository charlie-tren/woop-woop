# Woop Woop

The emptiest place you can get to.

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
    python build/au_build.py chunks     # per chunk: rasterise, two EDTs, mine peaks
    python build/au_build.py merge      # prune and pack into docs/data/peaks.*

Australia: 79 chunks, 78.9M vertices, about 25 minutes end to end. 1.09M raw peaks,
188k shipped, 5 MB.

Four traps this hit, all of them silent:

* **Seed the fine water flood from the OCEAN, never from `wet`.** Wet includes inland
  lakes, the fine labelling only splits on the coastline, so one lake cell drags the
  whole inland label under. Measured: 98.6% of a chunk came back as water, and 660
  peaks where there should have been 26,000.
* **Compute landmass components from the sea alone.** Including rivers made every
  capital its own landmass - Sydney answered 0.7 km whether you had one hour or four.
* **Component ids are ranked by size, so resolve an origin to the LOWEST id nearby**,
  not the nearest. Nearest put Brisbane on a sand island offshore.
* **Distances are stored in decametres.** A uint16 of metres tops out at 65.5 km and
  the best peak in Australia is 176.7 km, so metres clipped the entire desert to one
  number.

## Status

The emptiness measurement is exact and verified against an independent nearest-neighbour
search (agreement within 40 m, inside one cell). **The travel range is still a
straight-line estimate**; the real version calls a routing engine for an isochrone.
Coverage is all of Australia.

Data: [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, via
[Geofabrik](https://download.geofabrik.de/).
