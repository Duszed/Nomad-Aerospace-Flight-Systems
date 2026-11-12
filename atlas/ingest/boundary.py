"""Field boundary polygons (WGS-84 lon/lat).

Accepted files:
  * GeoJSON - Polygon, Feature or FeatureCollection (first polygon is used)
  * QGroundControl .plan - geofence polygon, else the first survey polygon
"""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import Polygon, shape
from shapely.validation import make_valid

from .track import IngestError


def _clean(poly) -> Polygon:
    if not poly.is_valid:
        poly = make_valid(poly)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    if poly.geom_type != "Polygon" or poly.is_empty:
        raise IngestError("field boundary is not a usable polygon")
    return poly


def boundary_from_geojson(obj: dict) -> Polygon:
    kind = obj.get("type")
    if kind == "FeatureCollection":
        for feat in obj.get("features", []):
            if (feat.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon"):
                return _clean(shape(feat["geometry"]))
        raise IngestError("GeoJSON FeatureCollection contains no polygon")
    if kind == "Feature":
        return _clean(shape(obj["geometry"]))
    if kind in ("Polygon", "MultiPolygon"):
        return _clean(shape(obj))
    raise IngestError(f"unsupported GeoJSON type {kind!r} for a field boundary")


def _boundary_from_qgc_plan(plan: dict) -> Polygon:
    fences = (plan.get("geoFence") or {}).get("polygons") or []
    for fence in fences:
        if fence.get("inclusion", True) and len(fence.get("polygon", [])) >= 3:
            return _clean(Polygon([(lon, lat) for lat, lon in fence["polygon"]]))
    for item in (plan.get("mission") or {}).get("items", []):
        ring = (item.get("TransectStyleComplexItem") or {}).get("polygon") \
            or item.get("polygon")
        if ring and len(ring) >= 3:
            return _clean(Polygon([(lon, lat) for lat, lon in ring]))
    raise IngestError("QGC plan has no geofence or survey polygon to use as the field boundary")


def load_boundary(path: Path) -> Polygon:
    path = Path(path)
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestError(f"{path.name}: cannot read field boundary ({exc})") from exc
    if obj.get("fileType") == "Plan" or "geoFence" in obj:
        return _boundary_from_qgc_plan(obj)
    return boundary_from_geojson(obj)
