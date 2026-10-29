"""ESG targets — the company's goals for its key figures.

A target says: move <metric> from <baseline value in baseline FY> to
<target value by target FY>. The figure for any year is computed from the
approved data sheets when the metric has a rule (app/admin/targets.py), or
typed in (``manual_values``) when it doesn't (e.g. Scope 1/2 emissions,
which come from the BRSR rather than a data sheet). A typed value for a
year also overrides the computed one, so a company can correct a figure
without re-uploading — the override is visible on the target.
"""
from datetime import datetime
from typing import Any, Optional

from sqlmodel import JSON, Column, Field, SQLModel

from app.core.public_ids import generate_public_id


def _tg_pid() -> str: return generate_public_id("tg_")


class EsgTarget(SQLModel, table=True):
    __tablename__ = "esg_targets"

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_tg_pid, unique=True, index=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    metric: str                      # catalog key, or "custom"
    title: str
    unit: str
    direction: str = Field(default="reduce")  # reduce | increase
    baseline_fy: str                 # "2023-24"
    baseline_value: float
    target_fy: str
    target_value: float
    manual_values: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    notes: Optional[str] = None
    is_active: bool = Field(default=True)
    created_by: int = Field(foreign_key="user.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
