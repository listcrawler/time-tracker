from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator


class Tag(BaseModel):
    name: str


class Entry(BaseModel):
    start: datetime | None = None
    end: datetime | None = None

    @field_validator("start", "end", mode="after")
    @classmethod
    def _require_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError(f"datetime must include timezone info: {v!r}")
        return v

    customer: str | None = None
    project: str | None = None
    description: str | None = None
    ticket: str | None = None
    ticket_url: str | None = None
    tags: list[Tag] | None = None
