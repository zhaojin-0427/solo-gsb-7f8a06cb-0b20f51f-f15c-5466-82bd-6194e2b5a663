"""FastAPI 入口：POST /resolve。"""
from __future__ import annotations

from typing import Any, Union

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .geometry import ParcelInputError
from .resolver import resolve_parcels

ParcelId = Union[str, int]


class ParcelIn(BaseModel):
    id: ParcelId
    priority: int
    geometry: dict[str, Any]

    model_config = {"extra": "ignore"}

    @field_validator("id", mode="before")
    @classmethod
    def _id_not_bool(cls, value: Any) -> Any:
        # bool 是 int 的子类，显式拒绝以免 id/优先级语义混乱。
        if isinstance(value, bool):
            raise ValueError("id 不能是布尔值")
        return value

    @field_validator("priority", mode="before")
    @classmethod
    def _priority_strict_int(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("priority 必须是整数")
        return value


class ResolveRequest(BaseModel):
    parcels: list[ParcelIn] = Field(
        ..., min_length=1, max_length=12, description="1～12 块地块"
    )

    model_config = {"extra": "ignore"}


app = FastAPI(
    title="本地地块叠置裁决 API",
    description="按优先级降序扣除、同层唯一/冲突裁决，保证面积守恒。",
    version="1.0.0",
)


@app.exception_handler(ParcelInputError)
async def _parcel_input_error_handler(_request: Request, exc: ParcelInputError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "invalid_parcel",
            "index": exc.index,
            "message": exc.message,
        },
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/resolve")
async def resolve(request: ResolveRequest) -> dict[str, Any]:
    raw = [parcel.model_dump() for parcel in request.parcels]
    return resolve_parcels(raw)
