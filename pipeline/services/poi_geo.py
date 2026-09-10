"""
Assign a POI point to a French commune (INSEE code) by point-in-polygon.

Commune contours come from geo.api.gouv.fr. That endpoint refuses an unfiltered
``geometry=contour`` call, so contours are fetched **one department at a time**
(same constraint ``seed_cities.py`` works around), merged into a single
FeatureCollection and cached under ``csv/poi/`` - re-used on later runs:
  - national scope  -> ``communes-contours.geojson``
  - ``--dept NN``    -> ``communes-contours-NN.geojson``  (much faster for tests)

Resolution strategy per point:
  1. STRtree bbox query over commune polygons, then exact ``.contains()`` test.
  2. Fallback for border / coastline points with no containing polygon:
     nearest commune by centroid distance, accepted only within ~2 km.
  3. Otherwise the POI is dropped.

Paris / Lyon / Marseille arrondissement codes are folded to the parent commune
(75056 / 69123 / 13055) via ``ARR_TO_COMMUNE``.
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from pathlib import Path

from shapely import STRtree
from shapely.geometry import Point, shape

from .geo import ARR_TO_COMMUNE, DEPT_TO_REGION

_CONTOUR_URL = (
    "https://geo.api.gouv.fr/communes"
    "?fields=code,contour&format=geojson&geometry=contour&codeDepartement={dept}"
)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CACHE_DIR = _REPO_ROOT / "csv" / "poi"

ALL_DEPARTMENTS = sorted(DEPT_TO_REGION.keys())

_NEAREST_MAX_M = 2000.0
_EARTH_R = 6371000.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_R * math.asin(math.sqrt(a))


def _fetch_department(dept: str) -> list[dict]:
    req = urllib.request.Request(
        _CONTOUR_URL.format(dept=dept),
        headers={"User-Agent": "immo-data-science/poi-ingest"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        gj = json.loads(r.read())
    return gj.get("features", [])


def _build_cache(dest: Path, dept_codes: list[str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    features: list[dict] = []
    for i, dept in enumerate(dept_codes, 1):
        for attempt in range(3):
            try:
                feats = _fetch_department(dept)
                break
            except Exception as e:  # noqa: BLE001 - transient network
                if attempt == 2:
                    raise
                print(f"    dept {dept} retry {attempt + 1} ({e})")
                time.sleep(2)
        features.extend(feats)
        print(f"    [{i}/{len(dept_codes)}] dept {dept}: {len(feats)} communes")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)
    tmp.replace(dest)
    print(f"  cached {len(features)} commune polygons -> {dest} ({dest.stat().st_size / 1e6:.1f} MB)")


class CommuneLocator:
    """Point -> INSEE commune code resolver backed by an STRtree."""

    def __init__(self, dept_codes: list[str] | None = None, geojson_path: str | Path | None = None):
        scope = sorted(dept_codes) if dept_codes else ALL_DEPARTMENTS
        if geojson_path is not None:
            path = Path(geojson_path)
        elif dept_codes:
            path = _CACHE_DIR / f"communes-contours-{'-'.join(scope)}.geojson"
        else:
            path = _CACHE_DIR / "communes-contours.geojson"

        if not path.exists():
            print(f"  fetching commune contours for {len(scope)} department(s) -> {path.name}")
            _build_cache(path, scope)

        with open(path, encoding="utf-8") as f:
            gj = json.load(f)

        self.codes: list[str] = []
        self.geoms: list = []
        self.centroids: list = []
        for feat in gj.get("features", []):
            code = (feat.get("properties") or {}).get("code")
            geom = feat.get("geometry")
            if not code or not geom:
                continue
            g = shape(geom)
            if g.is_empty:
                continue
            self.codes.append(code)
            self.geoms.append(g)
            self.centroids.append(g.representative_point())

        if not self.geoms:
            raise RuntimeError(f"No commune polygons parsed from {path}")

        self.tree = STRtree(self.geoms)
        self.centroid_tree = STRtree(self.centroids)
        self.stats = {"polygon": 0, "nearest": 0, "dropped": 0}

    def locate(self, lon: float, lat: float) -> str | None:
        """Return the INSEE code containing ``(lon, lat)`` or ``None``."""
        pt = Point(lon, lat)

        for idx in self.tree.query(pt):
            i = int(idx)
            if self.geoms[i].contains(pt):
                self.stats["polygon"] += 1
                return ARR_TO_COMMUNE.get(self.codes[i], self.codes[i])

        nidx = self.centroid_tree.nearest(pt)
        if nidx is not None:
            i = int(nidx)
            c = self.centroids[i]
            if _haversine_m(lat, lon, c.y, c.x) <= _NEAREST_MAX_M:
                self.stats["nearest"] += 1
                return ARR_TO_COMMUNE.get(self.codes[i], self.codes[i])

        self.stats["dropped"] += 1
        return None
