"""FastAPI application exposing the parcel overlay adjudication endpoint."""

from __future__ import annotations

from typing import Optional

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from .adjudicator import adjudicate
from .models import ErrorResponse, ResolveResponse
from .validation import InputError, validate_payload

app = FastAPI(
    title="Parcel Overlay Adjudicator",
    summary="本地地块叠置裁决 API：按优先级分层扣减，同层重叠归并为冲突区。",
    version="1.0.0",
)


def _geojson(geom: Optional[BaseGeometry]) -> Optional[dict]:
    if geom is None or geom.is_empty:
        return None
    return mapping(geom)


@app.exception_handler(InputError)
async def handle_input_error(_request: Request, exc: InputError) -> JSONResponse:
    return JSONResponse(
        status_code=400, content={"detail": "invalid input", "errors": exc.errors}
    )


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "detail": "invalid input",
            "errors": [
                {
                    "index": None,
                    "code": "invalid_payload",
                    "message": "request body must be a JSON object like {\"parcels\": [...]}",
                }
            ],
        },
    )


@app.get("/")
def root() -> dict:
    return {
        "service": "parcel-overlay-adjudicator",
        "endpoints": {"resolve": "POST /resolve", "docs": "/docs", "health": "/healthz"},
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post(
    "/resolve",
    response_model=ResolveResponse,
    responses={400: {"model": ErrorResponse, "description": "Invalid input"}},
)
def resolve(payload: dict = Body(...)) -> ResolveResponse:
    ids, priorities, geoms = validate_payload(payload)
    parcels, conflicts = adjudicate(ids, priorities, geoms)
    return ResolveResponse(
        parcels=[
            {
                "index": p.index,
                "id": p.parcel_id,
                "priority": p.priority,
                "original_area": p.original_area,
                "covered_by_higher_area": p.covered_by_higher_area,
                "conflict_area": p.conflict_area,
                "awarded_area": p.awarded_area,
                "awarded_geometry": _geojson(p.awarded_geometry),
            }
            for p in parcels
        ],
        conflicts=[
            {
                "parcel_ids": c.parcel_ids,
                "area": c.area,
                "geometry": mapping(c.geometry),
            }
            for c in conflicts
        ],
    )
