"""Field outlines for the dashboard: uploaded files, drawn points, areas, SVG maps.

Maps of results are drawn from the record's own geometry as inline SVG, so
they need no map tiles and always work offline.
"""

from __future__ import annotations

import json
import math
import re
from xml.etree import ElementTree

from pyproj import Transformer
from shapely.geometry import Polygon, shape

from ..compute.coverage import _reproject, to_geojson, utm_epsg
from ..ingest.boundary import _boundary_from_qgc_plan, _clean, boundary_from_geojson
from ..ingest.track import IngestError

MAX_BOUNDARY_BYTES = 5 * 1024 * 1024
MIN_AREA_HA, MAX_AREA_HA = 0.01, 5000.0


def area_ha(poly: Polygon) -> float:
    c = poly.centroid
    tr = Transformer.from_crs(4326, utm_epsg(c.x, c.y), always_xy=True)
    return _reproject(poly, tr).area / 10_000.0


def _check(poly: Polygon) -> Polygon:
    minx, miny, maxx, maxy = poly.bounds
    if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
        raise IngestError("coordinates are not longitude/latitude (WGS-84)")
    ha = area_ha(poly)
    if not MIN_AREA_HA <= ha <= MAX_AREA_HA:
        raise IngestError(f"the outline covers {ha:.2f} ha, which is not a plausible field")
    return poly


def boundary_from_points(points: list) -> Polygon:
    """From the draw tool: a list of [lat, lon] corners."""
    try:
        ring = [(float(lon), float(lat)) for lat, lon in points]
    except (TypeError, ValueError):
        raise IngestError("drawn outline is not a list of points") from None
    if len(ring) < 3:
        raise IngestError("an outline needs at least 3 corners")
    return _check(_clean(Polygon(ring)))


def boundary_from_kml(text: str) -> Polygon:
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise IngestError("this KML file uses features Atlas does not accept")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise IngestError(f"not a readable KML file ({exc})") from None
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "Polygon":
            continue
        for sub in el.iter():
            if sub.tag.rsplit("}", 1)[-1] == "coordinates" and sub.text:
                ring = []
                for chunk in sub.text.split():
                    parts = chunk.split(",")
                    if len(parts) >= 2:
                        ring.append((float(parts[0]), float(parts[1])))
                if len(ring) >= 3:
                    return _check(_clean(Polygon(ring)))
    raise IngestError("the KML file contains no polygon")


def boundary_from_upload(filename: str, data: bytes) -> Polygon:
    """GeoJSON, KML or a QGroundControl .plan, by content."""
    if len(data) > MAX_BOUNDARY_BYTES:
        raise IngestError("the file is too large for a field outline")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise IngestError("the file is not text") from None
    if filename.lower().endswith(".kml") or re.match(r"\s*<", text):
        return boundary_from_kml(text)
    try:
        obj = json.loads(text)
    except ValueError:
        raise IngestError("the file is neither GeoJSON nor KML") from None
    if not isinstance(obj, dict):
        raise IngestError("the file is not a GeoJSON object")
    try:
        if obj.get("fileType") == "Plan" or "geoFence" in obj:
            return _check(_boundary_from_qgc_plan(obj))
        return _check(boundary_from_geojson(obj))
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, IngestError):
            raise
        raise IngestError("the outline in the file is not valid") from None


def boundary_text(poly: Polygon) -> str:
    return json.dumps(to_geojson(poly), separators=(",", ":"))


def boundary_of(field) -> Polygon:
    return _clean(shape(json.loads(field.boundary_geojson)))


# --- SVG maps -----------------------------------------------------------------

def _rings(geojson: dict | None) -> list[list[list[float]]]:
    """All rings of a Polygon / MultiPolygon GeoJSON, as polygons of rings."""
    if not geojson:
        return []
    if geojson["type"] == "Polygon":
        return [geojson["coordinates"]]
    if geojson["type"] == "MultiPolygon":
        return list(geojson["coordinates"])
    return []


def _nice(metres: float) -> int:
    magnitude = 10 ** math.floor(math.log10(max(metres, 1)))
    for mult in (5, 2, 1):
        if mult * magnitude <= metres:
            return int(mult * magnitude)
    return int(magnitude)


def svg_map(field: dict, sprayed: dict | None = None, missed: dict | None = None,
            overlap: dict | None = None, width: int = 640, height: int = 460) -> str:
    """Inline SVG of a field and (optionally) its coverage layers."""
    polys = _rings(field)
    if not polys:
        return ""
    lons = [p[0] for poly in polys + _rings(sprayed) for ring in poly for p in ring]
    lats = [p[1] for poly in polys + _rings(sprayed) for ring in poly for p in ring]
    lat0 = (min(lats) + max(lats)) / 2
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    minx, maxx = min(lons) * kx, max(lons) * kx
    miny, maxy = min(lats) * ky, max(lats) * ky
    pad = 24
    scale = min((width - 2 * pad) / max(maxx - minx, 1e-6),
                (height - 2 * pad - 22) / max(maxy - miny, 1e-6))
    ox = (width - (maxx - minx) * scale) / 2
    oy = (height - 22 - (maxy - miny) * scale) / 2

    def path(geojson: dict | None) -> str:
        parts = []
        for poly in _rings(geojson):
            for ring in poly:
                pts = [f"{ox + (lon * kx - minx) * scale:.1f},"
                       f"{oy + (maxy - lat * ky) * scale:.1f}" for lon, lat in ring]
                parts.append("M" + "L".join(pts) + "Z")
        return "".join(parts)

    out = [f'<svg class="map" viewBox="0 0 {width} {height}" role="img" '
           f'xmlns="http://www.w3.org/2000/svg">']
    for css, geo in (("m-field", field), ("m-sprayed", sprayed), ("m-overlap", overlap),
                     ("m-missed", missed), ("m-outline", field)):
        d = path(geo)
        if d:
            out.append(f'<path class="{css}" fill-rule="evenodd" d="{d}"/>')
    bar_m = _nice((maxx - minx) / 3)
    bar_px = bar_m * scale
    y = height - 12
    out.append(f'<path class="m-scale" d="M{pad},{y - 5}V{y}H{pad + bar_px:.1f}V{y - 5}"/>')
    out.append(f'<text class="m-text" x="{pad + bar_px + 6:.1f}" y="{y}">{bar_m} m</text>')
    out.append("</svg>")
    return "".join(out)


def payload_map(payload: dict) -> str:
    geo = payload.get("geometry") or {}
    return svg_map(payload["field"]["boundary"], geo.get("sprayed"), geo.get("missed"),
                   geo.get("overlap"))


def field_map(field, width: int = 420, height: int = 240) -> str:
    return svg_map(json.loads(field.boundary_geojson), width=width, height=height)
