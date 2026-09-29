"""Pydantic response models for the API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ErrorItem(BaseModel):
    index: Optional[int] = None
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: str
    errors: list[ErrorItem]


class ParcelOutcome(BaseModel):
    index: int
    id: Any
    priority: int
    original_area: float
    covered_by_higher_area: float
    conflict_area: float
    awarded_area: float
    awarded_geometry: Optional[dict[str, Any]] = None


class ConflictZone(BaseModel):
    parcel_ids: list[Any]
    area: float
    geometry: dict[str, Any]


class ResolveResponse(BaseModel):
    parcels: list[ParcelOutcome]
    conflicts: list[ConflictZone]
