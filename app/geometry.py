"""GeoJSON 面几何的解析、校验与规范化辅助。"""
from __future__ import annotations

from typing import Any, Hashable, Iterator

from shapely import make_valid
from shapely.errors import GEOSException, GeometryTypeError
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize_full, unary_union

try:  # shapely>=2.0
    from shapely import is_valid_reason
except ImportError:  # pragma: no cover

    def is_valid_reason(geom: BaseGeometry) -> tuple[bool, str]:
        ok = geom.is_valid
        return ok, "Valid Geometry" if ok else "Invalid Geometry"


# 与任务语义对应的几何类型
_SURFACE_TYPES = {"Polygon", "MultiPolygon"}
_OTHER_GEOJSON_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "GeometryCollection",
    "Feature",
    "FeatureCollection",
}


class ParcelInputError(ValueError):
    """携带输入下标（0 基）的地块输入错误。"""

    def __init__(self, index: int, message: str):
        super().__init__(f"parcels[{index}]: {message}")
        self.index = index
        self.message = message


def parse_parcel_geometry(index: int, geojson: Any) -> BaseGeometry:
    """解析并校验单个 GeoJSON Polygon/MultiPolygon。

    对非面类型、空面、零面积面、自交等无效几何抛出 ParcelInputError，
    错误中带输入下标。
    """
    if not isinstance(geojson, dict):
        raise ParcelInputError(index, "geometry 必须是 GeoJSON Polygon/MultiPolygon 对象")

    gtype = geojson.get("type")
    if gtype not in _SURFACE_TYPES:
        if gtype in _OTHER_GEOJSON_TYPES:
            raise ParcelInputError(index, f"只接受 Polygon/MultiPolygon，收到 {gtype}")
        raise ParcelInputError(index, "geometry.type 必须是 Polygon 或 MultiPolygon")

    try:
        geom = shape(geojson)
    except (TypeError, ValueError, KeyError, GeometryTypeError, GEOSException, AttributeError) as exc:
        raise ParcelInputError(index, f"GeoJSON 几何无法解析：{exc}") from exc

    if geom.is_empty:
        raise ParcelInputError(index, "几何为空面（empty polygon）")
    if not isinstance(geom, (Polygon, MultiPolygon)):
        raise ParcelInputError(index, "几何必须是 Polygon 或 MultiPolygon")
    if not geom.is_valid:
        try:
            _ok, reason = is_valid_reason(geom)
        except Exception:  # pragma: no cover - 不同 GEOS 版本的兜底
            reason = "Invalid Geometry"
        raise ParcelInputError(index, f"几何无效（自交或环不闭合等）：{reason}")
    if geom.area <= 0.0:
        raise ParcelInputError(index, "面几何面积为 0")

    return geom


def _iter_polygons(geom: BaseGeometry | None) -> Iterator[Polygon]:
    """从 Polygon/MultiPolygon/GeometryCollection 中取出所有面片。"""
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        yield geom
        return
    for part in getattr(geom, "geoms", []):
        yield from _iter_polygons(part)


def sanitize_polygonal(geom: BaseGeometry | None) -> BaseGeometry | None:
    """把叠置运算结果整理成合法的 Polygon/MultiPolygon，丢弃线/点碎屑。

    结果为空时返回 None。
    """
    parts: list[Polygon] = []
    for poly in _iter_polygons(geom):
        if poly.is_empty or poly.area <= 0.0:
            continue
        if poly.is_valid:
            parts.append(poly)
            continue
        # 理论上 GEOS overlay 的结果总是合法的，这里做一层兜底。
        fixed = make_valid(poly)
        for candidate in _iter_polygons(fixed):
            if not candidate.is_empty and candidate.area > 0.0 and candidate.is_valid:
                parts.append(candidate)

    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    result = unary_union(parts)
    if result.is_empty or result.area <= 0.0:
        return None
    return result


def label_layer_faces(
    remnants: dict[Hashable, BaseGeometry],
) -> list[tuple[frozenset[Hashable], Polygon]]:
    """把同优先级各地块“余地”的并集切分为原子面，并标注覆盖它的地块 id 集合。

    做法：把所有余地的边界线合并、节点化后用 polygonize 枚举原子面；
    每个面取内点，统计哪些余地（的严格内部）覆盖该内点。
    共边、共点不会让多个 id 同时覆盖面内部，因此天然不算冲突；
    孔洞内部、外部区域没有任何覆盖者，会被丢弃。
    """
    items = [
        (pid, geom)
        for pid, geom in remnants.items()
        if geom is not None and not geom.is_empty and geom.area > 0.0
    ]
    if not items:
        return []

    boundaries = [geom.boundary for _, geom in items]
    noded = unary_union(boundaries)

    # polygonize_full 同时返回悬挂线、切面线等，只取形成的多边形面。
    faces_collection, _dangles, _cuts, _invalid_rings = polygonize_full(noded)
    faces = list(faces_collection.geoms)

    labeled: list[tuple[frozenset[Hashable], Polygon]] = []
    for face in faces:
        if face.is_empty or face.area <= 0.0:
            continue
        probe = face.representative_point()
        owners: set[Hashable] = set()
        for pid, geom in items:
            # 内点必须落在该余地的内部（落在其边界上不算覆盖）。
            if geom.covers(probe) and not geom.boundary.contains(probe):
                owners.add(pid)
        if owners:
            labeled.append((frozenset(owners), face))
    return labeled


def geojson_geometry(geom: BaseGeometry | None) -> dict | None:
    """Shapely 几何转 GeoJSON；None/空面转 null。"""
    if geom is None or geom.is_empty or geom.area <= 0.0:
        return None
    return mapping(geom)
