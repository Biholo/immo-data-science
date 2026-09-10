"""
Stream a Geofabrik ``.osm.pbf`` extract and yield whitelisted points of interest.

v1 scope:
  - **nodes** and **ways** only (relations are ignored).
  - ways use the arithmetic centroid of their node geometry and get a
    **negative** ``osm_id`` (``-way.id``) so they can never collide with node ids.
  - the ``name`` tag is mandatory - anything without a non-empty ``name`` is dropped.
  - only tags in the whitelist below survive; everything else is discarded, and
    each kept POI carries just ``osm_id, name, category, type, lat, lon, street``.

Node locations are cached (``apply_file(path, locations=True)``) so way centroids
resolve without a second pass.
"""

from __future__ import annotations

from typing import Callable

import osmium

# Keys that can possibly match the whitelist. Passed as an osmium C++ pre-filter
# to ``apply_file`` so the millions of untagged geometry nodes never reach the
# (slow) Python callbacks - cuts an Île-de-France pass from ~20 min to <1 min.
_WHITELIST_KEYS = ("amenity", "railway", "aeroway", "shop", "tourism", "leisure")

# -- Tag whitelist -> (category, type) ----------------------------------------
_EDUCATION = {"school", "kindergarten", "college", "university"}
_HEALTH = {"hospital", "clinic", "doctors", "pharmacy", "dentist"}
_CULTURE_AMENITY = {"library", "theatre", "cinema"}
_SERVICES_AMENITY = {"post_office", "bank", "police", "fire_station", "townhall"}
_AMENITY_TRANSPORT = {"bus_station": "bus_station", "ferry_terminal": "ferry_terminal"}
_RAILWAY_TRANSPORT = {"station": "train_station", "halt": "train_halt", "tram_stop": "tram_stop"}
_SHOP = {"supermarket", "mall", "department_store"}
_LEISURE = {"sports_centre", "stadium", "park"}

CATEGORIES = ("education", "health", "transport", "shopping", "culture", "leisure", "services")


def classify(tags) -> tuple[str, str] | None:
    """Map an OSM tag set to ``(category, type)`` or ``None`` if not whitelisted.

    ``tags`` only needs a ``.get(key)`` returning ``str | None`` (an osmium
    ``TagList`` works directly).
    """
    amenity = tags.get("amenity")
    if amenity:
        if amenity in _EDUCATION:
            return "education", amenity
        if amenity in _HEALTH:
            return "health", amenity
        if amenity in _AMENITY_TRANSPORT:
            return "transport", _AMENITY_TRANSPORT[amenity]
        if amenity in _CULTURE_AMENITY:
            return "culture", amenity
        if amenity in _SERVICES_AMENITY:
            return "services", amenity

    railway = tags.get("railway")
    if railway in _RAILWAY_TRANSPORT:
        return "transport", _RAILWAY_TRANSPORT[railway]

    if tags.get("aeroway") == "aerodrome":
        return "transport", "airport"

    shop = tags.get("shop")
    if shop in _SHOP:
        return "shopping", shop

    if tags.get("tourism") == "museum":
        return "culture", "museum"

    leisure = tags.get("leisure")
    if leisure in _LEISURE:
        return "leisure", leisure

    return None


def _way_centroid(way) -> tuple[float, float] | None:
    """Arithmetic mean of a way's valid node locations -> ``(lat, lon)``."""
    lats: list[float] = []
    lons: list[float] = []
    for nd in way.nodes:
        loc = nd.location
        if loc.valid():
            lats.append(loc.lat)
            lons.append(loc.lon)
    if not lats:
        return None
    return sum(lats) / len(lats), sum(lons) / len(lons)


class PoiHandler(osmium.SimpleHandler):
    """Collects whitelisted POIs via a callback ``on_poi(dict)``."""

    def __init__(self, on_poi: Callable[[dict], None]):
        super().__init__()
        self._on_poi = on_poi
        self.nodes_scanned = 0
        self.ways_scanned = 0
        self.kept = 0
        self.dropped_no_name = 0

    # -- osmium callbacks ---------------------------------------------------
    def node(self, n):
        self.nodes_scanned += 1
        loc = n.location
        self._emit(n.tags, n.id, (loc.lat, loc.lon) if loc.valid() else None)

    def way(self, w):
        self.ways_scanned += 1
        self._emit(w.tags, -w.id, _way_centroid(w))

    # -- shared -----------------------------------------------------------
    def _emit(self, tags, osm_id: int, coord: tuple[float, float] | None) -> None:
        cls = classify(tags)
        if cls is None:
            return
        name = tags.get("name")
        if not name or not name.strip():
            self.dropped_no_name += 1
            return
        if coord is None:
            return
        category, poi_type = cls
        self.kept += 1
        self._on_poi(
            {
                "osm_id": osm_id,
                "name": name.strip(),
                "category": category,
                "type": poi_type,
                "lat": coord[0],
                "lon": coord[1],
                "street": (tags.get("addr:street") or None),
            }
        )


def stream_pois(pbf_path: str) -> tuple[list[dict], dict[str, int]]:
    """Parse ``pbf_path`` once and return ``(pois, stats)``.

    ``stats`` keys: ``nodes_scanned``, ``ways_scanned``, ``kept``,
    ``dropped_no_name``.
    """
    pois: list[dict] = []
    handler = PoiHandler(pois.append)
    key_filter = osmium.filter.KeyFilter(*_WHITELIST_KEYS).enable_for(
        osmium.osm.NODE | osmium.osm.WAY
    )
    handler.apply_file(str(pbf_path), locations=True, filters=[key_filter])
    stats = {
        "nodes_scanned": handler.nodes_scanned,
        "ways_scanned": handler.ways_scanned,
        "kept": handler.kept,
        "dropped_no_name": handler.dropped_no_name,
    }
    return pois, stats
