"""地块叠置裁决核心算法。

裁决规则（按 priority 降序逐层处理）：
1. 本层每块地先扣除所有更高优先级已处理区域，得到“余地”；
2. 本层所有余地叠加后切分为原子面：
   - 仅被一块地覆盖的面，判归该地块（唯一获分）；
   - 被至少两块地覆盖的面，按“完全相同的参与 id 集合”归并为冲突区，
     不再交给任何更低优先级地块；
3. 本层余地并集并入已处理区域，继续处理下一层。
共边、共点不构成面覆盖，因此不算冲突；孔洞、多部件均正常处理。
"""
from __future__ import annotations

from typing import Any, Hashable

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .geometry import (
    ParcelInputError,
    geojson_geometry,
    label_layer_faces,
    parse_parcel_geometry,
    sanitize_polygonal,
)

AREA_EPS = 1e-12


def _clean_area(value: float) -> float:
    """清掉浮点误差产生的负零/极微小负值。"""
    if abs(value) < AREA_EPS:
        return 0.0
    return float(value)


def resolve_parcels(raw_parcels: list[dict[str, Any]]) -> dict[str, Any]:
    """对一批地块执行叠置裁决。

    输入为字典列表（id 唯一、priority 为整数、geometry 为面 GeoJSON）。
    返回可直接序列化为 JSON 的裁决结果。
    """
    if not (1 <= len(raw_parcels) <= 12):
        raise ParcelInputError(0, "地块数量必须在 1～12 块之间")

    # 先检查 id 唯一性（错误报在重复出现的输入下标处），再逐块解析几何。
    seen_ids: set[Hashable] = set()
    parsed: list[tuple[int, Hashable, int, BaseGeometry]] = []
    for index, raw in enumerate(raw_parcels):
        pid = raw["id"]
        if pid in seen_ids:
            raise ParcelInputError(index, f"id 重复：{pid!r}")
        seen_ids.add(pid)
        geom = parse_parcel_geometry(index, raw["geometry"])
        parsed.append((index, pid, int(raw["priority"]), geom))

    # 优先级降序分层；同优先级内部以输入顺序作为确定性次序。
    layers: dict[int, list[tuple[int, Hashable, BaseGeometry]]] = {}
    for index, pid, priority, geom in parsed:
        layers.setdefault(priority, []).append((index, pid, geom))

    original_area: dict[Hashable, float] = {
        pid: float(geom.area) for _i, pid, _p, geom in parsed
    }
    index_of: dict[Hashable, int] = {pid: i for i, pid, _p, _g in parsed}
    all_ids: list[Hashable] = [pid for _i, pid, _p, _g in parsed]

    covered_by_higher: dict[Hashable, float] = {pid: 0.0 for pid in all_ids}
    award_geoms: dict[Hashable, list[BaseGeometry]] = {pid: [] for pid in all_ids}
    # pid -> {冲突参与方集合 -> 冲突几何}
    conflict_parts: dict[frozenset[Hashable], list[BaseGeometry]] = {}
    # 参与方集合 -> 参与 id（保序、去重，用于输出）
    conflict_participants: dict[frozenset[Hashable], list[Hashable]] = {}

    processed: BaseGeometry | None = None  # 所有更高优先级已处理区域

    for priority in sorted(layers, reverse=True):
        members = layers[priority]

        # 1. 扣除更高优先级区域，得到本层各地块的余地。
        remnants: dict[Hashable, BaseGeometry] = {}
        for _index, pid, geom in members:
            if processed is None:
                remnant = geom
            else:
                diff = sanitize_polygonal(geom.difference(processed))
                if diff is None:
                    covered_by_higher[pid] = _clean_area(geom.area)
                    continue
                remnant = diff
                covered_by_higher[pid] = _clean_area(geom.area - remnant.area)
            remnants[pid] = remnant

        # 2. 本层余地叠加切分并标注覆盖者。
        faces = label_layer_faces(remnants)

        layer_union_parts: list[BaseGeometry] = []
        for owners, face in faces:
            layer_union_parts.append(face)
            if len(owners) >= 2:
                key = frozenset(owners)
                conflict_parts.setdefault(key, []).append(face)
                if key not in conflict_participants:
                    conflict_participants[key] = sorted(
                        owners, key=lambda x: index_of[x]
                    )
            else:
                (winner,) = owners
                award_geoms[winner].append(face)

        # 3. 本层余地并集并入已处理区域，阻止更低优先级进入。
        if remnants:
            layer_union = unary_union(list(remnants.values()))
            processed = layer_union if processed is None else unary_union([processed, layer_union])

    # 组装每块地的获分几何与统计。
    results: list[dict[str, Any]] = []
    for index, pid, _priority, _geom in parsed:
        award = sanitize_polygonal(
            unary_union(award_geoms[pid]) if award_geoms[pid] else None
        )
        award_area = _clean_area(award.area if award is not None else 0.0)

        same_layer_conflict_area = _clean_area(
            sum(
                unary_union(parts).area
                for owners, parts in conflict_parts.items()
                if pid in owners
            )
        )

        results.append(
            {
                "id": pid,
                "original_area": _clean_area(original_area[pid]),
                "covered_area": _clean_area(covered_by_higher[pid]),
                "conflict_area": same_layer_conflict_area,
                "awarded_area": award_area,
                "awarded_geometry": geojson_geometry(award),
            }
        )

    conflicts: list[dict[str, Any]] = []
    for owners, parts in conflict_parts.items():
        conflict_geom = sanitize_polygonal(unary_union(parts))
        conflicts.append(
            {
                "participant_ids": conflict_participants[owners],
                "area": _clean_area(conflict_geom.area if conflict_geom is not None else 0.0),
                "geometry": geojson_geometry(conflict_geom),
            }
        )
    # 面积大的在前；面积相同按参与方输入顺序的下标元组，保证输出稳定。
    conflicts.sort(
        key=lambda c: (
            -c["area"],
            tuple(index_of[pid] for pid in c["participant_ids"]),
        )
    )

    return {"parcels": results, "conflicts": conflicts}
