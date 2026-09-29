"""Core overlay-adjudication logic.

The plane is partitioned into atomic *cells*.  Each cell carries the set of
parcel indices covering it.  Inside a cell only the highest priority among
the covering parcels matters:

* exactly one top-priority parcel  -> the cell is awarded to it;
* two or more top-priority parcels -> the cell becomes a conflict zone
  attributed to exactly that set of parcel ids;
* every other (lower-priority) parcel covering the cell counts the cell's
  area as "covered by higher layers".

Shared edges / points have zero area and therefore never produce cells, so
they can never create conflicts.  Because every cell is assigned to exactly
one bucket per parcel, area is conserved per parcel:

    original_area == awarded_area + conflict_area + covered_by_higher_area
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


def _polygonal(geom: BaseGeometry) -> BaseGeometry:
    """Keep only the area-carrying (Polygon/MultiPolygon) parts of ``geom``.

    Overlay operations may return GeometryCollections containing lines/points
    (e.g. when inputs share an edge); those parts have zero area and are
    dropped so that only true surface pieces remain.
    """
    if geom.is_empty:
        return MultiPolygon()
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    parts = []
    for sub in getattr(geom, "geoms", ()):
        sub = _polygonal(sub)
        if not sub.is_empty:
            parts.append(sub)
    if not parts:
        return MultiPolygon()
    merged = unary_union(parts)
    return merged if not merged.is_empty else MultiPolygon()


def build_cells(geoms: list[BaseGeometry]) -> list[tuple[BaseGeometry, frozenset]]:
    """Partition the union of ``geoms`` into labelled atomic cells.

    Returns a list of ``(cell_geometry, frozenset(parcel_indices))`` where the
    set names every parcel covering that cell.  Cells are pairwise disjoint
    and their union equals the union of the inputs.
    """
    cells: list[tuple[BaseGeometry, frozenset]] = []
    covered: BaseGeometry = MultiPolygon()
    for idx, geom in enumerate(geoms):
        fresh = _polygonal(geom.difference(covered))
        if not fresh.is_empty:
            cells.append((fresh, frozenset((idx,))))
            covered = covered.union(fresh)
        for pos in range(len(cells)):
            cell_geom, owners = cells[pos]
            if idx in owners:
                continue
            overlap = _polygonal(cell_geom.intersection(geom))
            if overlap.is_empty:
                continue
            remainder = _polygonal(cell_geom.difference(geom))
            cells[pos] = (remainder, owners)
            cells.append((overlap, owners | {idx}))
    return [(g, owners) for g, owners in cells if not g.is_empty]


@dataclass
class ParcelResult:
    index: int
    parcel_id: Any
    priority: int
    original_area: float
    covered_by_higher_area: float = 0.0
    conflict_area: float = 0.0
    awarded_area: float = 0.0
    awarded_parts: list = field(default_factory=list)
    awarded_geometry: Optional[BaseGeometry] = None


@dataclass
class ConflictResult:
    indices: tuple
    parcel_ids: list
    area: float
    geometry: BaseGeometry


def adjudicate(
    ids: list, priorities: list[int], geoms: list[BaseGeometry]
) -> tuple[list[ParcelResult], list[ConflictResult]]:
    """Run the layered adjudication and return per-parcel and conflict results."""
    results = [
        ParcelResult(
            index=i,
            parcel_id=ids[i],
            priority=priorities[i],
            original_area=float(geoms[i].area),
        )
        for i in range(len(geoms))
    ]
    conflict_parts: dict[frozenset, list] = {}

    for cell_geom, owners in build_cells(geoms):
        area = float(cell_geom.area)
        top_priority = max(priorities[i] for i in owners)
        tops = frozenset(i for i in owners if priorities[i] == top_priority)
        if len(tops) == 1:
            (winner,) = tops
            results[winner].awarded_area += area
            results[winner].awarded_parts.append(cell_geom)
        else:
            conflict_parts.setdefault(tops, []).append(cell_geom)
        for i in owners:
            if i in tops:
                if len(tops) > 1:
                    results[i].conflict_area += area
            else:
                results[i].covered_by_higher_area += area

    conflicts = []
    for indices, parts in conflict_parts.items():
        geometry = unary_union(parts)
        conflicts.append(
            ConflictResult(
                indices=tuple(sorted(indices)),
                parcel_ids=[ids[i] for i in sorted(indices)],
                area=float(geometry.area),
                geometry=geometry,
            )
        )
    conflicts.sort(key=lambda c: c.indices)

    for result in results:
        if result.awarded_parts:
            result.awarded_geometry = unary_union(result.awarded_parts)

    return results, conflicts
