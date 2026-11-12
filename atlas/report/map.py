"""Coverage map: sprayed (green), missed (red), double-sprayed (amber).

Drawn with matplotlib from the geometry stored in the signed record, in the
field's UTM zone so one metre is one metre in both directions. No map tiles,
no network: it renders offline.
"""

from __future__ import annotations

import io
import math

import numpy as np
import shapely
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as MplPath
from pyproj import Transformer
from shapely.geometry import shape
from shapely.geometry.polygon import orient

from ..i18n import t

COLOR_FIELD = "#f4f1e8"
COLOR_BOUNDARY = "#2b2b2b"
COLOR_SPRAYED = "#3a9d5d"
COLOR_MISSED = "#d93025"
COLOR_OVERLAP = "#f5a623"


def _to_utm(geojson: dict | None, transformer: Transformer):
    if not geojson:
        return None

    def apply(coords):
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(shape(geojson), apply)


def _patch(geom, **style) -> PathPatch | None:
    """A matplotlib patch for a (Multi)Polygon, holes included."""
    if geom is None or geom.is_empty:
        return None
    polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    paths = []
    for poly in polys:
        if poly.geom_type != "Polygon" or poly.is_empty:
            continue
        poly = orient(poly, 1.0)  # exterior CCW, holes CW -> holes stay empty
        for ring in [poly.exterior, *poly.interiors]:
            paths.append(MplPath(np.asarray(ring.coords)[:, :2], closed=True))
    if not paths:
        return None
    return PathPatch(MplPath.make_compound_path(*paths), **style)


def _nice_scale(width_m: float) -> int:
    """A round scale-bar length of roughly a quarter of the map width."""
    target = max(width_m / 4.0, 1.0)
    magnitude = 10 ** math.floor(math.log10(target))
    for mult in (5, 2, 1):
        if mult * magnitude <= target:
            return int(mult * magnitude)
    return int(magnitude)


def render_map_png(payload: dict, lang: str = "en", width_in: float = 3.9,
                   height_in: float = 5.2, dpi: int = 200) -> bytes:
    """Render the coverage map of a record payload as PNG bytes."""
    epsg = payload["results"]["utm_epsg"]
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    field = _to_utm(payload["field"]["boundary"], tr)
    geo = payload["geometry"]
    sprayed = _to_utm(geo.get("sprayed"), tr)
    missed = _to_utm(geo.get("missed"), tr)
    overlap = _to_utm(geo.get("overlap"), tr)

    fig = Figure(figsize=(width_in, height_in), dpi=dpi)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0.02, 0.13, 0.96, 0.85])

    layers = [
        (field, dict(facecolor=COLOR_FIELD, edgecolor="none", zorder=1)),
        (sprayed, dict(facecolor=COLOR_SPRAYED, edgecolor="none", alpha=0.85, zorder=2)),
        (overlap, dict(facecolor=COLOR_OVERLAP, edgecolor="none", zorder=3)),
        (missed, dict(facecolor=COLOR_MISSED, edgecolor="none", zorder=4)),
        (field, dict(facecolor="none", edgecolor=COLOR_BOUNDARY, linewidth=1.3, zorder=5)),
    ]
    for geom, style in layers:
        patch = _patch(geom, **style)
        if patch is not None:
            ax.add_patch(patch)

    everything = [g for g in (field, sprayed) if g is not None and not g.is_empty]
    minx = min(g.bounds[0] for g in everything)
    miny = min(g.bounds[1] for g in everything)
    maxx = max(g.bounds[2] for g in everything)
    maxy = max(g.bounds[3] for g in everything)
    w, h = maxx - minx, maxy - miny
    pad = 0.06 * max(w, h)
    # Fit the padded extent into the axes box at one scale for both axes
    # (extra room at the bottom for the scale bar).
    need_w, need_h = w + 2 * pad, h + 3.2 * pad
    box_w, box_h = 0.96 * width_in, 0.85 * height_in
    m_per_in = max(need_w / box_w, need_h / box_h)
    span_w, span_h = box_w * m_per_in, box_h * m_per_in
    cx = (minx + maxx) / 2
    cy = (miny - 2.2 * pad + maxy + pad) / 2
    ax.set_xlim(cx - span_w / 2, cx + span_w / 2)
    ax.set_ylim(cy - span_h / 2, cy + span_h / 2)
    ax.axis("off")

    # scale bar (bottom left) and north arrow (top right)
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    bar = _nice_scale(x1 - x0)
    bx, by = x0 + 0.04 * (x1 - x0), y0 + 0.025 * (y1 - y0)
    ax.plot([bx, bx + bar], [by, by], color=COLOR_BOUNDARY, linewidth=2.2,
            solid_capstyle="butt", zorder=6)
    ax.text(bx + bar / 2, by + 0.012 * (y1 - y0), f"{bar} {t(lang, 'u_m')}",
            ha="center", va="bottom", fontsize=7, color=COLOR_BOUNDARY, zorder=6)
    nx, ny = x1 - 0.06 * (x1 - x0), y1 - 0.03 * (y1 - y0)
    ax.annotate("", xy=(nx, ny), xytext=(nx, ny - 0.09 * (y1 - y0)),
                arrowprops=dict(arrowstyle="-|>", color=COLOR_BOUNDARY, linewidth=1.2),
                zorder=6)
    ax.text(nx, ny - 0.10 * (y1 - y0), "N", ha="center", va="top", fontsize=8,
            fontweight="bold", color=COLOR_BOUNDARY, zorder=6)

    handles = [
        Patch(facecolor=COLOR_SPRAYED, alpha=0.85, label=t(lang, "legend_sprayed")),
        Patch(facecolor=COLOR_MISSED, label=t(lang, "legend_missed")),
        Patch(facecolor=COLOR_OVERLAP, label=t(lang, "legend_overlap")),
        Patch(facecolor=COLOR_FIELD, edgecolor=COLOR_BOUNDARY, linewidth=1.0,
              label=t(lang, "legend_boundary")),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=2, frameon=False,
               fontsize=7.5, handlelength=1.4, columnspacing=1.6,
               bbox_to_anchor=(0.5, 0.0))

    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=dpi, facecolor="white")
    return out.getvalue()
