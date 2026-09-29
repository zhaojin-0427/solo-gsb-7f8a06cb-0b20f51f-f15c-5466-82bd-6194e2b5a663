"""End-to-end tests for POST /resolve.

Run with: pytest (root of the repository).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import shape
from shapely.ops import unary_union

from app.main import app

client = TestClient(app)

REL = 1e-9


# --------------------------------------------------------------------------
# GeoJSON helpers
# --------------------------------------------------------------------------

def box(x1, y1, x2, y2):
    return {
        "type": "Polygon",
        "coordinates": [
            [[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]
        ],
    }


def parcel(pid, priority, geometry):
    return {"id": pid, "priority": priority, "geometry": geometry}


def post(parcels):
    return client.post("/resolve", json={"parcels": parcels})


def by_id(response, pid):
    return next(p for p in response.json()["parcels"] if p["id"] == pid)


# --------------------------------------------------------------------------
# Basic behaviour
# --------------------------------------------------------------------------

def test_health_and_root():
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/resolve", json={"parcels": []}).status_code == 400


def test_single_parcel_gets_everything():
    resp = post([parcel("a", 3, box(0, 0, 1, 1))])
    assert resp.status_code == 200, resp.json()
    p = resp.json()["parcels"][0]
    assert p["original_area"] == pytest.approx(1.0, abs=REL)
    assert p["awarded_area"] == pytest.approx(1.0, abs=REL)
    assert p["covered_by_higher_area"] == 0.0
    assert p["conflict_area"] == 0.0
    assert p["awarded_geometry"] is not None
    assert shape(p["awarded_geometry"]).area == pytest.approx(1.0, abs=REL)
    assert resp.json()["conflicts"] == []


def test_disjoint_same_priority_no_conflict():
    resp = post([parcel("a", 5, box(0, 0, 1, 1)),
                 parcel("b", 5, box(2, 0, 3, 1))])
    assert resp.status_code == 200
    body = resp.json()
    assert body["conflicts"] == []
    for p in body["parcels"]:
        assert p["awarded_area"] == pytest.approx(1.0, abs=REL)
        assert p["conflict_area"] == 0.0
        assert p["covered_by_higher_area"] == 0.0


def test_same_priority_overlap_is_conflict_grouped_by_ids():
    # A and B (same priority) overlap on [1,1]x[0,1], area 1.
    resp = post([parcel("a", 5, box(0, 0, 2, 1)),
                 parcel("b", 5, box(1, 0, 3, 1))])
    assert resp.status_code == 200
    body = resp.json()
    a, b = by_id(resp, "a"), by_id(resp, "b")
    assert a["awarded_area"] == pytest.approx(1.0, abs=REL)
    assert b["awarded_area"] == pytest.approx(1.0, abs=REL)
    assert a["conflict_area"] == b["conflict_area"] == pytest.approx(1.0, abs=REL)
    assert a["covered_by_higher_area"] == b["covered_by_higher_area"] == 0.0
    assert len(body["conflicts"]) == 1
    c = body["conflicts"][0]
    assert c["parcel_ids"] == ["a", "b"]
    assert c["area"] == pytest.approx(1.0, abs=REL)
    assert shape(c["geometry"]).area == pytest.approx(1.0, abs=REL)


def test_higher_priority_fully_covers_lower():
    resp = post([parcel("high", 9, box(0, 0, 2, 2)),
                 parcel("low", 1, box(0.5, 0.5, 1.5, 1.5))])
    body = resp.json()
    high, low = by_id(resp, "high"), by_id(resp, "low")
    assert high["awarded_area"] == pytest.approx(4.0, abs=REL)
    assert low["awarded_area"] == 0.0
    assert low["awarded_geometry"] is None
    assert low["covered_by_higher_area"] == pytest.approx(1.0, abs=REL)
    assert low["conflict_area"] == 0.0
    assert body["conflicts"] == []


def test_partial_higher_priority_cover():
    resp = post([parcel("high", 9, box(0, 0, 2, 1)),
                 parcel("low", 1, box(1, 0, 3, 1))])
    body = resp.json()
    high, low = by_id(resp, "high"), by_id(resp, "low")
    assert high["awarded_area"] == pytest.approx(2.0, abs=REL)
    assert high["covered_by_higher_area"] == 0.0
    assert low["awarded_area"] == pytest.approx(1.0, abs=REL)
    assert low["covered_by_higher_area"] == pytest.approx(1.0, abs=REL)
    assert shape(low["awarded_geometry"]).area == pytest.approx(1.0, abs=REL)
    assert body["conflicts"] == []


def test_shared_edge_does_not_conflict():
    resp = post([parcel("a", 5, box(0, 0, 1, 1)),
                 parcel("b", 5, box(1, 0, 2, 1))])
    body = resp.json()
    assert body["conflicts"] == []
    for p in body["parcels"]:
        assert p["awarded_area"] == pytest.approx(1.0, abs=REL)
        assert p["conflict_area"] == 0.0


def test_shared_point_does_not_conflict():
    resp = post([parcel("a", 5, box(0, 0, 1, 1)),
                 parcel("b", 5, box(1, 1, 2, 2))])
    body = resp.json()
    assert body["conflicts"] == []
    for p in body["parcels"]:
        assert p["awarded_area"] == pytest.approx(1.0, abs=REL)


def test_three_way_overlap_groups_by_exact_id_set():
    # A = [0,2]x[0,2], B = [1,3]x[0,2], C = [1.5,2.5]x[0.5,1.5] (C inside B).
    # Conflict groups expected:
    #   {a,b}      = [1,2]x[0,2] minus C -> area 1.5
    #   {a,b,c}    = [1.5,2]x[0.5,1.5]   -> area 0.5
    #   {b,c}      = [2,2.5]x[0.5,1.5]   -> area 0.5
    resp = post([parcel("a", 5, box(0, 0, 2, 2)),
                 parcel("b", 5, box(1, 0, 3, 2)),
                 parcel("c", 5, box(1.5, 0.5, 2.5, 1.5))])
    body = resp.json()
    groups = {tuple(c["parcel_ids"]): c["area"] for c in body["conflicts"]}
    assert set(groups) == {("a", "b"), ("a", "b", "c"), ("b", "c")}
    assert groups[("a", "b")] == pytest.approx(1.5, abs=REL)
    assert groups[("a", "b", "c")] == pytest.approx(0.5, abs=REL)
    assert groups[("b", "c")] == pytest.approx(0.5, abs=REL)
    a, b, c = by_id(resp, "a"), by_id(resp, "b"), by_id(resp, "c")
    assert a["awarded_area"] == pytest.approx(2.0, abs=REL)
    assert a["conflict_area"] == pytest.approx(2.0, abs=REL)
    assert b["awarded_area"] == pytest.approx(1.5, abs=REL)
    assert b["conflict_area"] == pytest.approx(2.5, abs=REL)
    assert c["awarded_area"] == 0.0
    assert c["awarded_geometry"] is None
    assert c["conflict_area"] == pytest.approx(1.0, abs=REL)


def test_conflict_zone_blocks_lower_priority():
    # a and b conflict on [1,2]x[0,2] (area 2); low-priority c covers exactly
    # that overlap and must be fully blocked without changing the conflict zone.
    resp = post([parcel("a", 5, box(0, 0, 2, 2)),
                 parcel("b", 5, box(1, 0, 3, 2)),
                 parcel("c", 1, box(1, 0, 2, 2))])
    body = resp.json()
    c = by_id(resp, "c")
    assert c["awarded_area"] == 0.0
    assert c["awarded_geometry"] is None
    assert c["covered_by_higher_area"] == pytest.approx(2.0, abs=REL)
    assert c["conflict_area"] == 0.0
    groups = {tuple(x["parcel_ids"]): x["area"] for x in body["conflicts"]}
    assert groups == {("a", "b"): pytest.approx(2.0, abs=REL)}


def test_polygon_with_hole_and_inner_parcel():
    donut = {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]],
            [[1, 1], [1, 2], [2, 2], [2, 1], [1, 1]],
        ],
    }
    resp = post([parcel("outer", 9, donut),
                 parcel("inner", 1, box(1, 1, 2, 2))])
    body = resp.json()
    outer, inner = by_id(resp, "outer"), by_id(resp, "inner")
    assert outer["original_area"] == pytest.approx(15.0, abs=REL)
    assert outer["awarded_area"] == pytest.approx(15.0, abs=REL)
    # The inner parcel sits in the hole, so it is not covered by the outer one.
    assert inner["awarded_area"] == pytest.approx(1.0, abs=REL)
    assert inner["covered_by_higher_area"] == 0.0
    assert body["conflicts"] == []


def test_multipolygon_split_by_higher_priority():
    multi = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            [[[5, 0], [6, 0], [6, 1], [5, 1], [5, 0]]],
        ],
    }
    blocker = box(0.2, 0.2, 0.8, 0.8)
    resp = post([parcel("high", 9, blocker), parcel("low", 1, multi)])
    body = resp.json()
    high, low = by_id(resp, "high"), by_id(resp, "low")
    assert high["awarded_area"] == pytest.approx(0.36, abs=REL)
    assert low["original_area"] == pytest.approx(2.0, abs=REL)
    assert low["covered_by_higher_area"] == pytest.approx(0.36, abs=REL)
    assert low["awarded_area"] == pytest.approx(1.64, abs=REL)
    awarded = shape(low["awarded_geometry"])
    assert awarded.geom_type == "MultiPolygon"
    assert len(awarded.geoms) == 2
    assert awarded.area == pytest.approx(1.64, abs=REL)


def test_negative_priorities_work_and_order_descending():
    resp = post([parcel("low", -5, box(0, 0, 1, 1)),
                 parcel("high", -1, box(0, 0, 1, 1))])
    body = resp.json()
    high, low = by_id(resp, "high"), by_id(resp, "low")
    assert high["awarded_area"] == pytest.approx(1.0, abs=REL)
    assert low["covered_by_higher_area"] == pytest.approx(1.0, abs=REL)
    assert low["awarded_geometry"] is None


def test_identical_same_priority_polygons_conflict_entirely():
    g = box(0, 0, 1, 1)
    resp = post([parcel("a", 2, g), parcel("b", 2, g)])
    body = resp.json()
    a, b = by_id(resp, "a"), by_id(resp, "b")
    assert a["awarded_area"] == 0.0 and b["awarded_area"] == 0.0
    assert a["awarded_geometry"] is None and b["awarded_geometry"] is None
    assert a["conflict_area"] == b["conflict_area"] == pytest.approx(1.0, abs=REL)
    assert len(body["conflicts"]) == 1
    assert shape(body["conflicts"][0]["geometry"]).area == pytest.approx(1.0, abs=REL)


def test_awarded_and_conflict_geometries_are_pairwise_disjoint():
    donut = {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [6, 0], [6, 6], [0, 6], [0, 0]],
            [[2, 2], [2, 3], [3, 3], [3, 2], [2, 2]],
        ],
    }
    parcels = [
        parcel("p1", 5, donut),
        parcel("p2", 5, box(1, 1, 5, 5)),
        parcel("p3", 3, box(4, 4, 8, 8)),
        parcel("p4", 7, box(0, 0, 1.5, 1.5)),
        parcel(
            "p5",
            3,
            {
                "type": "MultiPolygon",
                "coordinates": [
                    [[[-1, 2], [0, 2], [0, 3], [-1, 3], [-1, 2]]],
                    [[[5.5, 5.5], [7, 5.5], [7, 7], [5.5, 7], [5.5, 5.5]]],
                ],
            },
        ),
    ]
    resp = post(parcels)
    assert resp.status_code == 200, resp.json()
    body = resp.json()

    pieces = []
    for p in body["parcels"]:
        if p["awarded_geometry"] is not None:
            pieces.append(shape(p["awarded_geometry"]))
    for c in body["conflicts"]:
        pieces.append(shape(c["geometry"]))

    # Every pair of output surface pieces is interior-disjoint.
    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            inter = pieces[i].intersection(pieces[j])
            assert inter.area == pytest.approx(0.0, abs=REL)

    # Global conservation: awarded union + conflict union == input union.
    inputs = [shape(p["geometry"]) for p in parcels]
    input_union = unary_union(inputs)
    output_union = unary_union(pieces)
    assert output_union.area == pytest.approx(input_union.area, abs=1e-7)
    assert input_union.symmetric_difference(output_union).area == pytest.approx(0.0, abs=1e-7)

    # Per-parcel conservation: original == awarded + conflict + covered.
    for p in body["parcels"]:
        lhs = p["awarded_area"] + p["conflict_area"] + p["covered_by_higher_area"]
        assert lhs == pytest.approx(p["original_area"], abs=1e-7)

    # Summed areas match the grouped conflict geometry areas.
    for c in body["conflicts"]:
        summed = sum(
            p["conflict_area"] for p in body["parcels"] if p["id"] in c["parcel_ids"]
        )
        # Each participant counts the whole zone, so summed over participants
        # equals area * participant count.
        assert summed == pytest.approx(c["area"] * len(c["parcel_ids"]), abs=1e-7)


# --------------------------------------------------------------------------
# Validation / error reporting
# --------------------------------------------------------------------------

def test_too_many_parcels_reports_count_error():
    resp = post([parcel(f"p{i}", 1, box(0, 0, 1, 1)) for i in range(13)])
    assert resp.status_code == 400
    errs = resp.json()["errors"]
    assert any(e["code"] == "invalid_parcel_count" and e["index"] is None for e in errs)


def test_duplicate_id_reports_index():
    resp = post([parcel("same", 1, box(0, 0, 1, 1)),
                 parcel("other", 1, box(2, 2, 3, 3)),
                 parcel("same", 1, box(4, 4, 5, 5))])
    assert resp.status_code == 400
    errs = resp.json()["errors"]
    dup = [e for e in errs if e["code"] == "duplicate_id"]
    assert len(dup) == 1 and dup[0]["index"] == 2


def test_invalid_priority_types_reported():
    resp = post([
        parcel("a", 1.5, box(0, 0, 1, 1)),
        parcel("b", "high", box(0, 0, 1, 1)),
        parcel("c", True, box(0, 0, 1, 1)),
    ])
    assert resp.status_code == 400
    errs = resp.json()["errors"]
    bad = sorted(e["index"] for e in errs if e["code"] == "invalid_priority")
    assert bad == [0, 1, 2]


def test_non_polygonal_geometry_reports_index():
    resp = post([parcel("ok", 1, box(0, 0, 1, 1)),
                 parcel("pt", 1, {"type": "Point", "coordinates": [1, 1]})])
    assert resp.status_code == 400
    errs = resp.json()["errors"]
    bad = [e for e in errs if e["code"] == "non_polygonal_geometry"]
    assert len(bad) == 1 and bad[0]["index"] == 1


def test_empty_geometry_reported():
    resp = post([parcel("a", 1, {"type": "Polygon", "coordinates": []}),
                 parcel("b", 1, {"type": "MultiPolygon", "coordinates": []})])
    assert resp.status_code == 400
    errs = resp.json()["errors"]
    assert all(e["code"] == "empty_geometry" for e in errs)
    assert sorted(e["index"] for e in errs) == [0, 1]


def test_self_intersecting_bowtie_reported():
    bowtie = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]],
    }
    resp = post([parcel("ok", 1, box(0, 0, 1, 1)),
                 parcel("bow", 1, bowtie)])
    assert resp.status_code == 400
    errs = resp.json()["errors"]
    bad = [e for e in errs if e["code"] == "self_intersecting_geometry"]
    assert len(bad) == 1 and bad[0]["index"] == 1


def test_invalid_id_reported():
    resp = post([{"id": None, "priority": 1, "geometry": box(0, 0, 1, 1)}])
    assert resp.status_code == 400
    assert any(e["code"] == "invalid_id" and e["index"] == 0 for e in resp.json()["errors"])


def test_malformed_body_rejected():
    assert client.post("/resolve", json=[1, 2, 3]).status_code == 400
    assert client.post("/resolve", json={"nope": []}).status_code == 400
    r = client.post("/resolve", content="not json", headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_multiple_errors_returned_together_with_indices():
    resp = post([
        parcel("ok", 1, box(0, 0, 1, 1)),
        parcel("ok", "x", {"type": "Point", "coordinates": [0, 0]}),
    ])
    assert resp.status_code == 400
    codes = {e["code"] for e in resp.json()["errors"] if e["index"] == 1}
    assert {"invalid_priority", "non_polygonal_geometry"} <= codes
    assert any(e["code"] == "duplicate_id" for e in resp.json()["errors"])
