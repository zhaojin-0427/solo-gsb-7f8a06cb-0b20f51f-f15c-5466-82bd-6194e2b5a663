"""核心裁决算法测试：覆盖层间扣除、同层冲突、孔洞、多部件与面积守恒。"""
from __future__ import annotations

import random

import pytest
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape

from app.geometry import ParcelInputError
from app.resolver import resolve_parcels


def poly(x0, y0, x1, y1):
    return mapping(box(x0, y0, x1, y1))


def call(*parcels):
    return resolve_parcels([p for p in parcels])


def by_id(resp):
    return {p["id"]: p for p in resp["parcels"]}


def approx(x, y, tol=1e-7):
    return abs(x - y) <= tol


def assert_conservation(resp, inputs, tol=1e-7):
    """每块地：原面积 = 高层遮盖 + 同层冲突 + 获分。"""
    originals = {p["id"]: shape(p["geometry"]).area for p in inputs}
    rows = by_id(resp)
    for pid, original in originals.items():
        row = rows[pid]
        assert approx(row["original_area"], original, tol)
        assert approx(
            row["original_area"],
            row["covered_area"] + row["conflict_area"] + row["awarded_area"],
            tol,
        ), (pid, row)
    # 全局：所有原面积之和 = 所有唯一获分 + 所有冲突区（含重复计入，按每块地计）
    total_rows = sum(
        r["covered_area"] + r["conflict_area"] + r["awarded_area"]
        for r in rows.values()
    )
    # 冲突在参与方统计中被重复计入，这里校验每个冲突区的参与方数 >= 2
    for c in resp["conflicts"]:
        assert len(c["participant_ids"]) >= 2
    return total_rows


# ---------- 同优先级：重叠为冲突，其余唯一获分 ----------
def test_same_priority_overlap_is_conflict():
    a = {"id": "a", "priority": 1, "geometry": poly(0, 0, 2, 2)}
    b = {"id": "b", "priority": 1, "geometry": poly(1, 0, 3, 2)}
    resp = call(a, b)
    rows = by_id(resp)

    assert rows["a"]["awarded_area"] == pytest.approx(2.0)
    assert rows["b"]["awarded_area"] == pytest.approx(2.0)
    assert rows["a"]["conflict_area"] == pytest.approx(2.0)
    assert rows["b"]["conflict_area"] == pytest.approx(2.0)
    assert rows["a"]["covered_area"] == 0.0
    assert rows["b"]["covered_area"] == 0.0

    assert len(resp["conflicts"]) == 1
    conflict = resp["conflicts"][0]
    assert conflict["participant_ids"] == ["a", "b"]
    assert conflict["area"] == pytest.approx(2.0)
    assert shape(conflict["geometry"]).area == pytest.approx(2.0)
    assert_conservation(resp, [a, b])


# ---------- 高优先级整块遮盖低优先级 ----------
def test_higher_priority_covers_lower():
    hi = {"id": "hi", "priority": 2, "geometry": poly(0, 0, 2, 2)}
    lo = {"id": "lo", "priority": 1, "geometry": poly(0, 0, 2, 2)}
    resp = call(hi, lo)
    rows = by_id(resp)

    assert rows["hi"]["awarded_area"] == pytest.approx(4.0)
    assert rows["hi"]["covered_area"] == 0.0
    assert rows["lo"]["covered_area"] == pytest.approx(4.0)
    assert rows["lo"]["awarded_area"] == 0.0
    assert rows["lo"]["awarded_geometry"] is None
    assert resp["conflicts"] == []
    assert_conservation(resp, [hi, lo])


# ---------- 共边、共点不算冲突 ----------
def test_shared_edge_and_point_not_conflict():
    a = {"id": "a", "priority": 1, "geometry": poly(0, 0, 1, 1)}
    b = {"id": "b", "priority": 1, "geometry": poly(1, 0, 2, 1)}  # 共边 x=1
    c = {"id": "c", "priority": 1, "geometry": poly(1, 1, 2, 2)}  # 与 a 共点 (1,1)
    resp = call(a, b, c)
    rows = by_id(resp)

    for pid in ("a", "b", "c"):
        assert rows[pid]["awarded_area"] == pytest.approx(1.0), pid
        assert rows[pid]["conflict_area"] == 0.0
    assert resp["conflicts"] == []
    assert_conservation(resp, [a, b, c])


# ---------- 三块同层：唯一 / 双方冲突 / 三方冲突互不混入 ----------
def test_three_parcels_uni_pairwise_triple_conflict():
    a = {"id": "a", "priority": 1, "geometry": poly(0, 0, 2, 2)}
    b = {"id": "b", "priority": 1, "geometry": poly(1, 0, 3, 2)}
    c = {"id": "c", "priority": 1, "geometry": poly(0, 1, 2, 3)}
    resp = call(a, b, c)
    rows = by_id(resp)

    # a: 原面积 4 = 唯一 1 + {a,b} 2 + {a,c} 2 + {a,b,c} 1 - 重叠修正
    assert rows["a"]["awarded_area"] == pytest.approx(1.0)
    assert rows["b"]["awarded_area"] == pytest.approx(2.0)
    assert rows["c"]["awarded_area"] == pytest.approx(2.0)

    # 3x3 网格中按原子面归属：
    # a 参与 {a,b}=1、{a,c}=1、{a,b,c}=1，共 3
    assert rows["a"]["conflict_area"] == pytest.approx(3.0)
    assert rows["b"]["conflict_area"] == pytest.approx(2.0)
    assert rows["c"]["conflict_area"] == pytest.approx(2.0)

    sets = {tuple(c["participant_ids"]): c["area"] for c in resp["conflicts"]}
    assert sets[("a", "b")] == pytest.approx(1.0)
    assert sets[("a", "c")] == pytest.approx(1.0)
    assert sets[("a", "b", "c")] == pytest.approx(1.0)
    assert_conservation(resp, [a, b, c])


# ---------- 相同参与 id 集合的两块冲突区被归并 ----------
def test_conflicts_with_same_participant_set_are_merged():
    # 两个独立的重叠区域，参与方都是 a,b，应归并为一条冲突记录
    a1 = poly(0, 0, 2, 2)
    a2 = poly(10, 0, 12, 2)
    a_geom = mapping(MultiPolygon([box(0, 0, 2, 2), box(10, 0, 12, 2)]))
    a = {"id": "a", "priority": 1, "geometry": a_geom}
    b_geom = mapping(MultiPolygon([box(1, 0, 3, 2), box(11, 0, 13, 2)]))
    b = {"id": "b", "priority": 1, "geometry": b_geom}
    resp = call(a, b)

    assert len(resp["conflicts"]) == 1
    assert resp["conflicts"][0]["participant_ids"] == ["a", "b"]
    assert resp["conflicts"][0]["area"] == pytest.approx(4.0)  # 两块各 2
    merged = shape(resp["conflicts"][0]["geometry"])
    assert merged.geom_type == "MultiPolygon"
    assert_conservation(resp, [a, b])


# ---------- 孔洞：外环重叠但一方有孔 ----------
def test_holes_are_handled():
    # b 为带孔的大方块，孔内区域（2x2）只有 a 覆盖
    ring_outer = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    ring_inner = [(4, 4), (4, 6), (6, 6), (6, 4), (4, 4)]
    b_geom = mapping(Polygon(ring_outer, holes=[ring_inner]))
    a = {"id": "a", "priority": 1, "geometry": poly(0, 0, 10, 10)}
    b = {"id": "b", "priority": 1, "geometry": b_geom}
    resp = call(a, b)
    rows = by_id(resp)

    assert rows["a"]["awarded_area"] == pytest.approx(4.0)          # 孔内
    assert rows["b"]["awarded_area"] == pytest.approx(0.0)
    assert rows["a"]["conflict_area"] == pytest.approx(96.0)       # 环形重叠
    assert rows["b"]["conflict_area"] == pytest.approx(96.0)
    assert shape(rows["a"]["awarded_geometry"]).area == pytest.approx(4.0)
    assert_conservation(resp, [a, b])


# ---------- 高优先级之间的冲突也会阻挡低优先级 ----------
def test_higher_layer_conflict_blocks_lower():
    hi1 = {"id": "hi1", "priority": 2, "geometry": poly(0, 0, 2, 2)}
    hi2 = {"id": "hi2", "priority": 2, "geometry": poly(0, 0, 2, 2)}
    lo = {"id": "lo", "priority": 1, "geometry": poly(0, 0, 2, 2)}
    resp = call(hi1, hi2, lo)
    rows = by_id(resp)

    assert rows["hi1"]["awarded_area"] == 0.0
    assert rows["hi1"]["conflict_area"] == pytest.approx(4.0)
    assert rows["hi2"]["conflict_area"] == pytest.approx(4.0)
    assert rows["lo"]["covered_area"] == pytest.approx(4.0)
    assert rows["lo"]["awarded_geometry"] is None
    assert len(resp["conflicts"]) == 1
    assert set(resp["conflicts"][0]["participant_ids"]) == {"hi1", "hi2"}
    assert_conservation(resp, [hi1, hi2, lo])


# ---------- 部分遮盖：低优先级拿到差集 ----------
def test_partial_cover_leaves_remainder():
    hi = {"id": "hi", "priority": 2, "geometry": poly(0, 0, 2, 2)}
    lo = {"id": "lo", "priority": 1, "geometry": poly(1, 0, 4, 2)}
    resp = call(hi, lo)
    rows = by_id(resp)

    assert rows["hi"]["awarded_area"] == pytest.approx(4.0)
    assert rows["lo"]["covered_area"] == pytest.approx(2.0)
    assert rows["lo"]["awarded_area"] == pytest.approx(4.0)
    assert rows["lo"]["awarded_geometry"] is not None
    assert_conservation(resp, [hi, lo])


# ---------- 随机矩形压力测试：面积守恒与裁决互斥性 ----------
@pytest.mark.parametrize("seed", [1, 2, 3, 7, 42])
def test_random_rectangles_conservation(seed):
    rng = random.Random(seed)
    inputs = []
    for i in range(12):
        x0 = rng.uniform(-50, 150)
        y0 = rng.uniform(-50, 150)
        w = rng.uniform(1, 80)
        h = rng.uniform(1, 80)
        inputs.append(
            {
                "id": f"p{i}",
                "priority": rng.randint(0, 4),
                "geometry": mapping(box(x0, y0, x0 + w, y0 + h)),
            }
        )
    resp = resolve_parcels(inputs)
    assert_conservation(resp, inputs, tol=1e-6)

    # 所有输出几何必须是合法面，且总面积等于输入并集面积（每点恰好归属一次）。
    rows = by_id(resp)
    covered_total = sum(shape(r["awarded_geometry"]).area if r["awarded_geometry"] else 0.0
                        for r in rows.values())
    conflict_total = sum(shape(c["geometry"]).area for c in resp["conflicts"])
    union_area = unary_union_of_inputs(inputs)
    assert covered_total + conflict_total == pytest.approx(union_area, abs=1e-6)

    # 任意两块“获分几何”内部互不重叠；获分与冲突区内部也互不重叠。
    pieces = []
    for r in rows.values():
        if r["awarded_geometry"]:
            pieces.append(shape(r["awarded_geometry"]))
    for c in resp["conflicts"]:
        pieces.append(shape(c["geometry"]))
    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            inter = pieces[i].intersection(pieces[j])
            assert inter.is_empty or inter.area == pytest.approx(0.0, abs=1e-9)


def unary_union_of_inputs(inputs):
    from shapely.ops import unary_union

    return unary_union([shape(p["geometry"]) for p in inputs]).area


# ---------- 输入校验：错误带输入下标（0 基） ----------
def test_self_intersecting_polygon_index_reported():
    good = {"id": "ok", "priority": 1, "geometry": poly(0, 0, 1, 1)}
    bowtie = {
        "id": "bad",
        "priority": 1,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)]],
        },
    }
    with pytest.raises(ParcelInputError) as exc:
        call(good, bowtie)
    assert exc.value.index == 1


def test_empty_polygon_index_reported():
    empty = {"id": "e", "priority": 1, "geometry": {"type": "Polygon", "coordinates": []}}
    with pytest.raises(ParcelInputError) as exc:
        call(empty)
    assert exc.value.index == 0


def test_non_polygon_index_reported():
    point = {
        "id": "p",
        "priority": 1,
        "geometry": {"type": "Point", "coordinates": [0, 0]},
    }
    with pytest.raises(ParcelInputError) as exc:
        call(point)
    assert exc.value.index == 0


def test_duplicate_id_index_reported():
    a = {"id": "x", "priority": 1, "geometry": poly(0, 0, 1, 1)}
    b = {"id": "x", "priority": 1, "geometry": poly(0, 0, 1, 1)}
    with pytest.raises(ParcelInputError) as exc:
        call(a, b)
    assert exc.value.index == 1


def test_zero_area_polygon_rejected():
    line_like = {
        "id": "z",
        "priority": 1,
        "geometry": {"type": "Polygon", "coordinates": [[(0, 0), (1, 0), (0, 0)]],},
    }
    with pytest.raises(ParcelInputError):
        call(line_like)
