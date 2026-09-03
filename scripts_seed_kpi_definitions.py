"""Seed the initial KPI definitions (Layer 1).

Only the SLOTS — a name, an informational unit hint, and which upload
type/domain each belongs to. NO formulas, weights, emission factors, or
scoring logic of any kind. Real methodology (Layer 2) plugs in later when
the manager-approved KPI catalogue arrives; this seed does not anticipate
or guess at it.

Derived from the existing domainGuidance columns already reviewed and
approved as a provisional v1 seed (not the final ESG methodology) — see
the Layer 1 architecture decisions in app/models/kpi.py's module
docstring for the full reasoning behind which quantities became their own
KPI code versus a qualifying attribute tag.

Aligned with the same MVP KPI scope as upload_types: Energy, Water,
Emissions, Waste (see scripts_seed_upload_types.py).

EXPLICIT CLARIFICATIONS, per the post-launch independent review:

1. Water genuinely defines TWO separate KPI codes — water.withdrawal and
   water.recycled — not one. This is not an oversight or a duplicate; a
   single reported water record legitimately contains two distinct
   measured quantities (how much was taken, how much was put back), and
   each gets its own structured, independently-queryable KPI code and
   its own KpiValue row.

2. This catalog defines STRUCTURED KPI METRICS within the four approved
   MVP domains only — it is NOT, and must never be mistaken for, an ESG
   scoring, rating, or benchmarking system. There is no weighting, no
   aggregation across metrics, no pillar/dimension composite, and no
   pass/fail threshold anywhere in this file or in the KpiDefinition
   model it populates. Each row here names one measurable, real-world
   quantity a company reports — nothing more.
"""
from sqlmodel import Session, select
from app.db.session import engine
from app.models.upload_type import UploadType
from app.models.kpi import KpiDefinition


# code -> (display_name, unit_hint). unit_hint is informational only —
# never enforced or converted; the actual per-row unit is always whatever
# the uploader's file specifies (see KpiValue.unit in app/models/kpi.py).
KPI_DEFINITIONS = {
    "energy_data": [
        ("energy.consumption", "Energy Consumption", "kWh"),
    ],
    "water_data": [
        ("water.withdrawal", "Water Withdrawn", "ML"),
        ("water.recycled", "Water Recycled", "ML"),
    ],
    "emissions_data": [
        # No single natural unit hint — activity data varies by scope/type
        # (liters of fuel, kWh of electricity, km traveled, etc).
        # Deliberately left null rather than guessing one.
        ("emissions.activity_data", "Activity Data", None),
    ],
    "waste_data": [
        ("waste.generated", "Waste Generated", "t"),
    ],
}


def seed_kpi_definitions(session: Session) -> dict:
    count = 0
    upload_type_by_code = {
        ut.code: ut for ut in session.exec(select(UploadType)).all()
    }
    for upload_type_code, defs in KPI_DEFINITIONS.items():
        ut = upload_type_by_code.get(upload_type_code)
        if not ut:
            # Upload type doesn't exist yet — skip rather than fail the
            # whole seed; run scripts_seed_upload_types.py first.
            continue
        for code, display_name, unit_hint in defs:
            existing = session.exec(
                select(KpiDefinition).where(
                    KpiDefinition.code == code, KpiDefinition.version == 1
                )
            ).first()
            if not existing:
                session.add(KpiDefinition(
                    code=code, display_name=display_name, unit_hint=unit_hint,
                    upload_type_id=ut.id, data_type="numeric", version=1,
                ))
                count += 1
    session.commit()
    return {"kpi_definitions_added": count}


if __name__ == "__main__":
    with Session(engine) as s:
        result = seed_kpi_definitions(s)
        print("Seeded:", result)
