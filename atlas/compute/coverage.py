"""Step 2 - COMPUTE: what was actually sprayed, from a track and a boundary.

Method
------
1. Project everything to the field's UTM zone (chosen automatically from the
   field centroid) so areas and buffers are in true metres.
2. Every stretch flown with the sprayer on is split into passes (a new pass
   starts when the heading turns more than 45 degrees) and each pass is
   buffered by swath/2 with flat ends. The union is the sprayed area.
3. Double-sprayed = ground covered by two or more passes.
4. Missed = field minus sprayed. Gaps and overlaps narrower than
   2 x `sliver_m` are ignored: they are below positioning accuracy.
5. Litres: measured from a flow meter if the log has one, else the
   operator-reported tank volume if given, else ESTIMATED as
   sprayed distance x swath x planned rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..ingest.track import IngestError, Track

MAX_STEP_M = 200.0       # larger jump between fixes = break in the track
MAX_GAP_S = 10.0         # longer silence between fixes = break in the track
PASS_TURN_DEG = 45.0     # heading change that starts a new pass
SIMPLIFY_M = 0.05        # output geometry simplification tolerance

LITRES_MEASURED = "measured"      # flow meter
LITRES_OPERATOR = "operator"      # operator-reported tank volume
LITRES_ESTIMATED = "estimated"    # distance x swath x planned rate


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the WGS-84 UTM zone containing a point."""
    zone = min(60, max(1, int(math.floor((lon + 180.0) / 6.0)) + 1))
    return (32600 if lat >= 0 else 32700) + zone


@dataclass
class CoverageResult:
    utm_epsg: int
    swath_m: float
    field_area_ha: float
    sprayed_area_ha: float          # unique ground sprayed, in or out of the field
    covered_in_field_ha: float
    coverage_pct: float
    missed_area_ha: float
    missed_strips: int
    overlap_area_ha: float
    outside_field_ha: float
    sprayed_distance_m: float
    transit_distance_m: float
    spray_time_s: float
    flight_time_s: float
    avg_spray_speed_ms: float
    avg_spray_height_m: float | None
    passes: int
    litres_applied: float
    litres_basis: str
    planned_rate_l_ha: float
    planned_litres: float
    actual_rate_l_ha: float
    rate_deviation_pct: float
    # geometries in WGS-84 lon/lat
    field_geom: BaseGeometry = field(repr=False, default=None)
    sprayed_geom: BaseGeometry = field(repr=False, default=None)
    missed_geom: BaseGeometry = field(repr=False, default=None)
    overlap_geom: BaseGeometry = field(repr=False, default=None)

    def numbers(self) -> dict:
        """Rounded figures, exactly as they go into the signed record."""
        return {
            "field_area_ha": round(self.field_area_ha, 4),
            "sprayed_area_ha": round(self.sprayed_area_ha, 4),
            "covered_in_field_ha": round(self.covered_in_field_ha, 4),
            "coverage_pct": round(self.coverage_pct, 2),
            "missed_area_ha": round(self.missed_area_ha, 4),
            "missed_strips": self.missed_strips,
            "overlap_area_ha": round(self.overlap_area_ha, 4),
            "outside_field_ha": round(self.outside_field_ha, 4),
            "sprayed_distance_m": round(self.sprayed_distance_m, 1),
            "transit_distance_m": round(self.transit_distance_m, 1),
            "spray_time_s": round(self.spray_time_s, 1),
            "flight_time_s": round(self.flight_time_s, 1),
            "avg_spray_speed_ms": round(self.avg_spray_speed_ms, 2),
            "avg_spray_height_m": (None if self.avg_spray_height_m is None
                                   else round(self.avg_spray_height_m, 2)),
            "passes": self.passes,
            "litres_applied": round(self.litres_applied, 2),
            "litres_basis": self.litres_basis,
            "planned_rate_l_ha": round(self.planned_rate_l_ha, 2),
            "planned_litres": round(self.planned_litres, 2),
            "actual_rate_l_ha": round(self.actual_rate_l_ha, 2),
            "rate_deviation_pct": round(self.rate_deviation_pct, 1),
            "utm_epsg": self.utm_epsg,
        }

    def geojson(self) -> dict:
        return {
            "field": to_geojson(self.field_geom),
            "sprayed": to_geojson(self.sprayed_geom),
            "missed": to_geojson(self.missed_geom),
            "overlap": to_geojson(self.overlap_geom),
        }


def to_geojson(geom: BaseGeometry | None, ndigits: int = 7) -> dict | None:
    """GeoJSON dict with coordinates rounded (stable for canonical JSON)."""
    if geom is None or geom.is_empty:
        return None

    def rnd(x):
        if isinstance(x, (list, tuple)):
            return [rnd(v) for v in x]
        return round(float(x), ndigits)

    m = mapping(geom)
    if m["type"] == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        return to_geojson(unary_union(polys), ndigits) if polys else None
    return {"type": m["type"], "coordinates": rnd(m["coordinates"])}


def _reproject(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    def apply(coords):
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])
    return shapely.transform(geom, apply)


def _polygons_only(geom: BaseGeometry) -> BaseGeometry:
    """Drop stray lines/points that set operations can leave behind."""
    if geom.is_empty or geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    if hasattr(geom, "geoms"):
        return unary_union([g for g in geom.geoms
                            if g.geom_type in ("Polygon", "MultiPolygon")] or [Polygon()])
    return Polygon()


def _open(geom: BaseGeometry, sliver_m: float) -> BaseGeometry:
    """Morphological opening: removes parts narrower than 2 x sliver_m."""
    if geom.is_empty or sliver_m <= 0:
        return geom
    # mitred joins keep square corners square; the intersection guarantees the
    # result never grows beyond the original shape
    opened = geom.buffer(-sliver_m, join_style="mitre").buffer(sliver_m, join_style="mitre")
    return _polygons_only(opened.intersection(geom))


def _parts(geom: BaseGeometry) -> list[Polygon]:
    if geom.is_empty:
        return []
    return list(geom.geoms) if hasattr(geom, "geoms") else [geom]


def _split_passes(run: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Split one sprayer-on run into passes at sharp heading changes."""
    passes: list[list[tuple[float, float]]] = []
    current = [run[0]]
    ref_heading = None
    for a, b in zip(run, run[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if dx == 0 and dy == 0:
            continue
        heading = math.degrees(math.atan2(dx, dy))
        if ref_heading is None:
            ref_heading = heading
        elif abs((heading - ref_heading + 180.0) % 360.0 - 180.0) > PASS_TURN_DEG:
            passes.append(current)
            current = [a]
            ref_heading = heading
        current.append(b)
    passes.append(current)
    return [p for p in passes if len(p) >= 2]


def compute_coverage(track: Track, boundary: Polygon, swath_m: float,
                     planned_rate_l_ha: float,
                     tank_litres_used: float | None = None,
                     sliver_m: float = 0.25,
                     min_strip_m2: float = 5.0) -> CoverageResult:
    """Compute the as-applied coverage of `track` over `boundary` (lon/lat)."""
    if swath_m <= 0:
        raise ValueError("swath must be positive")
    if planned_rate_l_ha <= 0:
        raise ValueError("planned rate must be positive")
    if len(track.samples) < 2:
        raise IngestError("track has fewer than two samples")

    c = boundary.centroid
    epsg = utm_epsg(c.x, c.y)
    fwd = Transformer.from_crs(4326, epsg, always_xy=True)
    inv = Transformer.from_crs(epsg, 4326, always_xy=True)

    field_utm = _reproject(boundary, fwd)
    xs, ys = fwd.transform([s.lon for s in track.samples],
                           [s.lat for s in track.samples])
    pts = list(zip(xs, ys))
    smp = track.samples

    # --- walk the track ------------------------------------------------------
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] | None = None
    sprayed_dist = transit_dist = spray_time = 0.0
    flow_litres = 0.0
    speed_sum = height_sum = 0.0
    speed_n = height_n = 0

    for i in range(1, len(smp)):
        a, b = pts[i - 1], pts[i]
        step = math.hypot(b[0] - a[0], b[1] - a[1])
        dt = smp[i].t - smp[i - 1].t
        if step > MAX_STEP_M or dt < 0 or dt > MAX_GAP_S:
            current = None          # break in the data: never bridge it
            continue
        if smp[i - 1].flow_lpm is not None:
            flow_litres += smp[i - 1].flow_lpm * dt / 60.0
        if smp[i - 1].spray_on:
            if current is None:
                current = [a]
                runs.append(current)
            current.append(b)
            sprayed_dist += step
            spray_time += dt
            speed_sum += smp[i - 1].speed_ms
            speed_n += 1
            if smp[i - 1].height_m is not None:
                height_sum += smp[i - 1].height_m
                height_n += 1
        else:
            current = None
            transit_dist += step

    # --- geometry ------------------------------------------------------------
    passes = [p for run in runs for p in _split_passes(run)]
    buffers = []
    for p in passes:
        line = LineString(p)
        if line.length > 0:
            buffers.append(line.buffer(swath_m / 2.0, cap_style="flat",
                                       join_style="round"))

    sprayed = _polygons_only(unary_union(buffers)) if buffers else Polygon()

    overlaps = []
    if len(buffers) > 1:
        tree = STRtree(buffers)
        for i, buf in enumerate(buffers):
            for j in tree.query(buf, predicate="intersects"):
                if j > i:
                    inter = buf.intersection(buffers[int(j)])
                    if not inter.is_empty and inter.area > 0:
                        overlaps.append(inter)
    overlap = _open(_polygons_only(unary_union(overlaps)), sliver_m) if overlaps else Polygon()

    missed_raw = _open(_polygons_only(field_utm.difference(sprayed)), sliver_m)
    missed_parts = [g for g in _parts(missed_raw) if g.area >= min_strip_m2]
    missed = unary_union(missed_parts) if missed_parts else Polygon()

    outside = _polygons_only(sprayed.difference(field_utm))

    field_area = field_utm.area
    missed_area = missed.area
    covered_area = field_area - missed_area
    sprayed_ha = sprayed.area / 10_000.0

    # --- litres --------------------------------------------------------------
    planned_litres = planned_rate_l_ha * field_area / 10_000.0
    if track.has_flow:
        litres, basis = flow_litres, LITRES_MEASURED
    elif tank_litres_used is not None:
        litres, basis = float(tank_litres_used), LITRES_OPERATOR
    else:
        litres = sprayed_dist * swath_m / 10_000.0 * planned_rate_l_ha
        basis = LITRES_ESTIMATED
    actual_rate = litres / sprayed_ha if sprayed_ha > 0 else 0.0
    deviation = (actual_rate - planned_rate_l_ha) / planned_rate_l_ha * 100.0

    def back(geom):
        if geom.is_empty:
            return geom
        return _reproject(geom.simplify(SIMPLIFY_M), inv)

    return CoverageResult(
        utm_epsg=epsg,
        swath_m=swath_m,
        field_area_ha=field_area / 10_000.0,
        sprayed_area_ha=sprayed_ha,
        covered_in_field_ha=covered_area / 10_000.0,
        coverage_pct=100.0 * covered_area / field_area if field_area else 0.0,
        missed_area_ha=missed_area / 10_000.0,
        missed_strips=len(missed_parts),
        overlap_area_ha=overlap.area / 10_000.0,
        outside_field_ha=outside.area / 10_000.0,
        sprayed_distance_m=sprayed_dist,
        transit_distance_m=transit_dist,
        spray_time_s=spray_time,
        flight_time_s=track.duration_s,
        avg_spray_speed_ms=speed_sum / speed_n if speed_n else 0.0,
        avg_spray_height_m=height_sum / height_n if height_n else None,
        passes=len(buffers),
        litres_applied=litres,
        litres_basis=basis,
        planned_rate_l_ha=planned_rate_l_ha,
        planned_litres=planned_litres,
        actual_rate_l_ha=actual_rate,
        rate_deviation_pct=deviation,
        field_geom=boundary,
        sprayed_geom=back(sprayed),
        missed_geom=back(missed),
        overlap_geom=back(overlap),
    )
