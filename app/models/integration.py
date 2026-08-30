"""Integration stub (Stage 4).

Adapter BOUNDARY only, no live connectors. Approved decision 3.

Enough persistence to demo the concept — a client can register that they
"have SAP" and see it listed — without committing to any specific integration
implementation. `config` deliberately excludes secrets; when a real connector
arrives, credentials live in a separate secrets table or in Secret Manager.
"""
from datetime import datetime
from typing import Optional, Any
from sqlmodel import SQLModel, Field, Column, Index
from sqlalchemy import JSON


class Integration(SQLModel, table=True):
    __tablename__ = "integrations"
    __table_args__ = (
        Index("ix_integration_company_type", "company_id", "type"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    type: str  # 'sap' | 'workday' | 'oracle' | ...
    status: str = Field(default="configured")  # 'configured'|'disabled'|'error'
    config: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    last_sync_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
