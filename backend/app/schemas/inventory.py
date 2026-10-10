from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InventoryStatus(str, Enum):
    available = "available"
    reserved = "reserved"
    in_use = "in_use"
    damaged = "damaged"
    missing = "missing"
    maintenance = "maintenance"
    unavailable = "unavailable"


def _strip_text(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


class InventoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    status: InventoryStatus = InventoryStatus.available

    @field_validator("name", "category", "description", mode="before")
    @classmethod
    def strip_text(cls, value: Any) -> Any:
        return _strip_text(value)


class InventoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    attributes: dict[str, str | int | float | bool | None] | None = None
    status: InventoryStatus | None = None

    @field_validator("name", "category", "description", mode="before")
    @classmethod
    def strip_text(cls, value: Any) -> Any:
        return _strip_text(value)


class InventoryItem(BaseModel):
    inventoryId: str
    name: str
    category: str
    description: str
    attributes: dict[str, Any]
    status: InventoryStatus
    createdAt: datetime
    updatedAt: datetime


class InventoryList(BaseModel):
    items: list[InventoryItem]
    total: int
