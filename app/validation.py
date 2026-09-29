"""Request payload validation for POST /resolve.

All problems are reported as HTTP 400 with a body of the shape::

    {"detail": "invalid input",
     "errors": [{"index": <input position or null>, "code": ..., "message": ...}]}

``index`` is the zero-based position of the offending parcel in the input
array (``null`` for payload-level problems).
"""

from __future__ import annotations

import math
from typing import Any, Optional

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

try:  # Shapely >= 2.0
    from shapely import force_2d
except ImportError:  # pragma: no cover

    def force_2d(geom):
        return geom


MAX_PARCELS = 12
ALLOWED_GEOMETRY_TYPES = {"Polygon", "MultiPolygon"}
_ID_TYPES = (str, int)


class InputError(Exception):
    """Raised when the request payload fails validation."""

    def __init__(self, errors: list[dict[str, Any]]):
        super().__init__("invalid input")
        self.errors = errors


def _err(index: Optional[int], code: str, message: str) -> dict[str, Any]:
    return {"index": index, "code": code, "message": message}


def _finite_coordinates(coords: Any) -> bool:
    """Recursively verify that every coordinate is a finite number."""
    if isinstance(coords, (list, tuple)):
        return all(_finite_coordinates(item) for item in coords)
    return (
        isinstance(coords, (int, float))
        and not isinstance(coords, bool)
        and math.isfinite(coords)
    )


def parse_geometry(raw: Any, index: int, errors: list) -> Optional[BaseGeometry]:
    """Validate one parcel geometry; append problems to ``errors``."""
    if not isinstance(raw, dict):
        errors.append(
            _err(index, "invalid_geometry", "geometry must be a GeoJSON geometry object")
        )
        return None
    gtype = raw.get("type")
    if gtype not in ALLOWED_GEOMETRY_TYPES:
        errors.append(
            _err(
                index,
                "non_polygonal_geometry",
                f"geometry type must be Polygon or MultiPolygon, got {gtype!r}",
            )
        )
        return None
    if "coordinates" not in raw or not _finite_coordinates(raw.get("coordinates")):
        errors.append(
            _err(
                index,
                "invalid_geometry",
                "coordinates are missing, malformed or contain non-finite numbers",
            )
        )
        return None
    try:
        geom = shape(raw)
    except Exception as exc:
        errors.append(_err(index, "invalid_geometry", f"cannot parse geometry: {exc}"))
        return None
    if geom.is_empty:
        errors.append(_err(index, "empty_geometry", "geometry has no area (empty surface)"))
        return None
    if not geom.is_valid:
        errors.append(
            _err(
                index,
                "self_intersecting_geometry",
                f"geometry is invalid (self-intersection or malformed ring): "
                f"{explain_validity(geom)}",
            )
        )
        return None
    # Areas and overlays are planar; drop any Z ordinate from the output path.
    return force_2d(geom)


def validate_payload(payload: Any) -> tuple[list, list[int], list[BaseGeometry]]:
    """Validate the whole request body.

    Returns ``(ids, priorities, geometries)`` on success, raises
    :class:`InputError` otherwise.
    """
    if not isinstance(payload, dict):
        raise InputError(
            [_err(None, "invalid_payload", "request body must be a JSON object")]
        )
    raw_parcels = payload.get("parcels")
    if not isinstance(raw_parcels, list):
        raise InputError(
            [_err(None, "invalid_payload", "'parcels' must be an array of parcels")]
        )
    if not 1 <= len(raw_parcels) <= MAX_PARCELS:
        raise InputError(
            [
                _err(
                    None,
                    "invalid_parcel_count",
                    f"'parcels' must contain between 1 and {MAX_PARCELS} items, "
                    f"got {len(raw_parcels)}",
                )
            ]
        )

    errors: list = []
    ids: list = []
    priorities: list[int] = []
    geoms: list[Optional[BaseGeometry]] = []
    seen_ids: set = set()

    for i, raw in enumerate(raw_parcels):
        if not isinstance(raw, dict):
            errors.append(
                _err(i, "invalid_parcel", "parcel must be an object with id, priority and geometry")
            )
            ids.append(None)
            priorities.append(0)
            geoms.append(None)
            continue

        pid = raw.get("id")
        if pid is None or isinstance(pid, bool) or not isinstance(pid, _ID_TYPES):
            errors.append(
                _err(i, "invalid_id", "'id' is required and must be a string or an integer")
            )
        elif pid in seen_ids:
            errors.append(_err(i, "duplicate_id", f"duplicate id {pid!r}"))
        else:
            seen_ids.add(pid)

        prio = raw.get("priority")
        if isinstance(prio, bool) or not isinstance(prio, int):
            errors.append(_err(i, "invalid_priority", "'priority' must be an integer"))

        geom = parse_geometry(raw.get("geometry"), i, errors)

        ids.append(pid)
        priorities.append(prio if isinstance(prio, int) and not isinstance(prio, bool) else 0)
        geoms.append(geom)

    if errors:
        raise InputError(errors)
    return ids, priorities, geoms
