"""Upload type registry (Stage 4).

Metadata-only. Defines WHAT can be uploaded to WHICH slot — not HOW to validate
its contents. Real template columns and business rules plug in later without
schema change.

`allowed_mime_types` is a JSON array so a slot can accept e.g. both xlsx and
xls without normalizing to a canonical value.
"""
from datetime import datetime
from typing import Optional, List
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class UploadType(SQLModel, table=True):
    __tablename__ = "upload_types"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(unique=True, index=True)  # 'energy_data', 'water_evidence'
    display_name: str
    purpose: Optional[str] = None
    allowed_mime_types: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    max_file_size_bytes: int = Field(default=25 * 1024 * 1024)  # 25MB default
    # Nullable refs — the actual template/validation contract arrives later.
    template_ref: Optional[str] = None
    validation_policy_ref: Optional[str] = None
    processing_mode: str = Field(default="async")  # "sync" | "async"
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
