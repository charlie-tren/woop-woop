"""The world, one region at a time.

Geofabrik publishes the planet as continent extracts. Each one goes through exactly the
pipeline Australia did - bucket, coarse, chunks, merge - and produces its own peak file.
Nothing here is new logic; it is the same three stages with the box and the paths as
parameters.

Regions are SHARDED in the output rather than merged into one file. A world peak list
would be tens of megabytes and nobody starting in Brisbane needs Norway's fire trails,
so the client loads the shard its origin falls in. That also keeps component ids
region-local, which is fine: they answer "can I drive there", and you cannot drive
between these boxes anyway.

Ordered smallest-first so coverage lands incrementally instead of after everything.
"""

REGIONS = {
    # name:        (south, west, north, east)                      extract slug
    "antarctica":  ((-90.0, -180.0, -60.0, 180.0),  "antarctica"),
    "central-america": ((5.0, -95.0, 24.0, -58.0),  "central-america"),
    "australia":   ((-44.5, 112.0, -9.0, 154.5),    "australia-oceania"),
    "new-zealand": ((-47.5, 166.0, -34.0, 179.0),   "australia-oceania"),
    "south-america": ((-56.0, -82.0, 13.0, -34.0),  "south-america"),
    "africa":      ((-35.0, -18.0, 38.0, 52.0),     "africa"),
    "north-america": ((14.0, -170.0, 72.0, -52.0),  "north-america"),
    "europe":      ((34.0, -25.0, 71.5, 45.0),      "europe"),
    "asia":        ((-11.0, 25.0, 78.0, 180.0),     "asia"),
}

# Rough order of cost, so a run can be stopped between regions and still have shipped
# something useful. Sizes are the compressed extract, which is the parsing cost.
ORDER = ["antarctica", "central-america", "new-zealand", "south-america", "africa",
         "north-america", "europe", "asia"]

EXTRACT_URL = "https://download.geofabrik.de/{slug}-latest.osm.pbf"


def bbox(name):
    return REGIONS[name][0]


def slug(name):
    return REGIONS[name][1]


def url(name):
    return EXTRACT_URL.format(slug=slug(name))
