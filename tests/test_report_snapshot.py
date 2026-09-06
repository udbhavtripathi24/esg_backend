"""Report snapshot resolution tests -- the 8 mandatory scenarios from the
implementation authorization, plus content-hash determinism/immutability
tests.

Reuses the exact same helper functions already established in
test_layer1_kpi.py.
"""
from datetime import date
from sqlmodel import select

from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from tests.test_layer1_kpi import (
    _energy_xlsx, _set_storage, _seed, _submitted_version, _approve, _run_kpi_job,
)
from app.models.dataset import Dataset, DatasetVersion
from app.services.report_snapshot_service import resolve_snapshot_inputs, compute_content_hash


def _approve_and_extract(client, session, uploader, reviewer, co, upload_type, xlsx):
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, upload_type, xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    v = _run_kpi_job(session, v_pid)
    return ds_pid, v_pid, v


# ---------- 1. approved V1 -> snapshot uses V1 ----------

def test_approved_v1_snapshot_uses_v1(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "s1u@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "s1r@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid, v1 = _approve_and_extract(client, session, uploader, reviewer, co, "energy_data", xlsx)

    result = resolve_snapshot_inputs(session, co.id, date(2026, 4, 1), date(2026, 6, 30))
    assert result["dataset_version_ids"] == [v1.id]
    assert len(result["kpi_value_ids"]) == 1


# ---------- 2. approved V1 + draft V2 -> snapshot still uses V1 ----------

def test_approved_v1_plus_draft_v2_uses_v1(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "s2u@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "s2r@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v1_pid, v1 = _approve_and_extract(client, session, uploader, reviewer, co, "energy_data", xlsx)

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(uploader))
    assert r.status_code == 201  # v2 created, left as draft -- never submitted/approved

    result = resolve_snapshot_inputs(session, co.id, date(2026, 4, 1), date(2026, 6, 30))
    assert result["dataset_version_ids"] == [v1.id]


# ---------- 3. approved V1 + changes_requested V2 -> snapshot still uses V1 ----------

def test_approved_v1_plus_changes_requested_v2_uses_v1(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "s3u@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "s3r@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v1_pid, v1 = _approve_and_extract(client, session, uploader, reviewer, co, "energy_data", xlsx)

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(uploader))
    v2_pid = r.json()["public_id"]
    xlsx2 = _energy_xlsx([["Site A", "electricity", 999, "kWh", "Q2 2026"]])
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v2_pid}/files",
               files={"file": ("d2.xlsx", xlsx2, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
               data={"role": "data"}, headers=auth(uploader))
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v2_pid}/submit", headers=auth(uploader))
    r2 = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v2_pid}/reviews", json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv2_pid = r2.json()["public_id"]
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v2_pid}/reviews/{rv2_pid}/decide",
               json={"decision": "changes_requested", "note": "Fix it."}, headers=auth(reviewer))

    result = resolve_snapshot_inputs(session, co.id, date(2026, 4, 1), date(2026, 6, 30))
    assert result["dataset_version_ids"] == [v1.id]
    kv = session.exec(select(Dataset).where(Dataset.public_id == ds_pid)).first()


# ---------- 4. approved V1 + approved V2 -> snapshot uses V2 ----------

def test_approved_v1_plus_approved_v2_uses_v2(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "s4u@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "s4r@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v1_pid, v1 = _approve_and_extract(client, session, uploader, reviewer, co, "energy_data", xlsx)

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(uploader))
    v2_pid = r.json()["public_id"]
    xlsx2 = _energy_xlsx([["Site A", "electricity", 999, "kWh", "Q2 2026"]])
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v2_pid}/files",
               files={"file": ("d2.xlsx", xlsx2, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
               data={"role": "data"}, headers=auth(uploader))
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v2_pid}/submit", headers=auth(uploader))
    _approve(client, session, uploader, reviewer, ds_pid, v2_pid)
    v2 = _run_kpi_job(session, v2_pid)

    result = resolve_snapshot_inputs(session, co.id, date(2026, 4, 1), date(2026, 6, 30))
    assert result["dataset_version_ids"] == [v2.id]
    assert v1.id not in result["dataset_version_ids"]


# ---------- 5. submitted V2 without approval -> snapshot does not use V2 ----------

def test_submitted_v2_without_approval_not_used(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "s5u@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "s5r@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v1_pid, v1 = _approve_and_extract(client, session, uploader, reviewer, co, "energy_data", xlsx)

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(uploader))
    v2_pid = r.json()["public_id"]
    xlsx2 = _energy_xlsx([["Site A", "electricity", 999, "kWh", "Q2 2026"]])
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v2_pid}/files",
               files={"file": ("d2.xlsx", xlsx2, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
               data={"role": "data"}, headers=auth(uploader))
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v2_pid}/submit", headers=auth(uploader))
    # Deliberately NOT approved -- stays 'submitted'

    result = resolve_snapshot_inputs(session, co.id, date(2026, 4, 1), date(2026, 6, 30))
    assert result["dataset_version_ids"] == [v1.id]


# ---------- 6. no approved data -> empty, not fabricated ----------

def test_no_approved_data_returns_empty(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    result = resolve_snapshot_inputs(session, co.id, date(2026, 4, 1), date(2026, 6, 30))
    assert result["dataset_version_ids"] == []
    assert result["kpi_value_ids"] == []
    assert result["evidence_file_ids"] == []


# ---------- 7. multiple datasets -> exact authoritative versions selected ----------

def test_multiple_datasets_each_resolved_independently(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "s7u@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "s7r@d.com", "deloitte", "Reviewer", org=org)

    xlsx_e = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    _, _, v_energy = _approve_and_extract(client, session, uploader, reviewer, co, "energy_data", xlsx_e)

    from tests.test_layer1_kpi import _water_xlsx
    xlsx_w = _water_xlsx([["Site A", "groundwater", 50, 10, "ML", "Q2 2026"]])
    _, _, v_water = _approve_and_extract(client, session, uploader, reviewer, co, "water_data", xlsx_w)

    result = resolve_snapshot_inputs(session, co.id, date(2026, 4, 1), date(2026, 6, 30))
    assert set(result["dataset_version_ids"]) == {v_energy.id, v_water.id}
    assert len(result["kpi_value_ids"]) == 3  # 1 energy + 2 water (withdrawal + recycled)


# ---------- 8. MANDATORY: current_version_id trap ----------

def test_current_version_id_deliberately_wrong_snapshot_still_correct(client, session, tmp_path):
    """MANDATORY per the implementation authorization. Deliberately
    corrupts Dataset.current_version_id to point at the WRONG (draft)
    version, proving resolve_snapshot_inputs never reads that field at
    all and still selects the genuinely approved version."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "s8u@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "s8r@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v1_pid, v1 = _approve_and_extract(client, session, uploader, reviewer, co, "energy_data", xlsx)

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(uploader))
    v2_pid = r.json()["public_id"]
    v2 = session.exec(select(DatasetVersion).where(DatasetVersion.public_id == v2_pid)).first()
    assert v2.status == "draft"

    # Deliberately corrupt current_version_id to point at the draft, not the approved version.
    ds = session.exec(select(Dataset).where(Dataset.public_id == ds_pid)).first()
    ds.current_version_id = v2.id  # WRONG on purpose
    session.add(ds); session.commit()

    result = resolve_snapshot_inputs(session, co.id, date(2026, 4, 1), date(2026, 6, 30))
    assert result["dataset_version_ids"] == [v1.id]  # correct, despite the corrupted pointer
    assert v2.id not in result["dataset_version_ids"]


# ---------- Content hash: determinism and drift detection ----------

def test_content_hash_deterministic_for_same_ids(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "chu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "chr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    _, _, v1 = _approve_and_extract(client, session, uploader, reviewer, co, "energy_data", xlsx)

    result = resolve_snapshot_inputs(session, co.id, date(2026, 4, 1), date(2026, 6, 30))
    hash1 = compute_content_hash(session, result["kpi_value_ids"])
    hash2 = compute_content_hash(session, result["kpi_value_ids"])
    assert hash1 == hash2  # same input, same hash, every time


def test_content_hash_empty_list_handled(session):
    h = compute_content_hash(session, [])
    assert h is not None and len(h) == 64  # valid sha256 hex digest, no crash on empty input
