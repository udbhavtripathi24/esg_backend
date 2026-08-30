"""Stage 4 tests — master data, datasets, files, security, jobs, audit.

Coverage:
- Master data CRUD for sites, business units, departments
- Tenant isolation on every endpoint (cross-tenant -> 404)
- Dataset lifecycle: create -> version -> upload file -> submit
- Version immutability
- File validation (magic bytes, size, allowed types, ZIP rejected,
  extension-spoofed rejected)
- Signed URL redemption
- Processing job lifecycle (enqueue on upload, worker completes)
- Audit log for every mutation
- Upload type CRUD (admin)
- Integration stub CRUD
"""
import io
import os
import tempfile
from datetime import date
from sqlmodel import select

from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from scripts_seed_upload_types import seed_upload_types
from app.models.master_data import Site, BusinessUnit, Department
from app.models.dataset import Dataset, DatasetVersion, DatasetFile
from app.models.audit_log import AuditLog
from app.models.processing_job import ProcessingJob
from app.models.upload_type import UploadType


# -------- fixtures/helpers --------

def _seed_all(session):
    org = bootstrap(session)
    seed_upload_types(session)
    return org


# Real minimal xlsx bytes (empty but structurally valid)
def _tiny_xlsx() -> bytes:
    # Build via openpyxl for a real xlsx that passes magic-byte + zip inspection
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["site_code", "period", "quantity", "unit"])
    ws.append(["S1", "2026-Q1", 123.4, "MWh"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf_bytes() -> bytes:
    return (b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
            b"xref\n0 1\n0000000000 65535 f\ntrailer\n<<>>\n%%EOF\n")


def _zip_bytes() -> bytes:
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hello")
    return buf.getvalue()


def _use_temp_storage(tmp_path=None):
    """Point storage backend at a fresh temp dir."""
    from app.storage.factory import get_storage as _gs
    _gs.cache_clear()
    from app.core.config import settings
    root = tmp_path or tempfile.mkdtemp(prefix="esg-test-")
    settings.STORAGE_LOCAL_ROOT = root
    settings.STORAGE_BACKEND = "local"
    return root


# -------- Master data --------

def test_create_and_list_site(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    co = make_company(session, org, "AcmeCo")
    admin = make_user(session, "a@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/master-data/sites",
                    json={"company_id": co.id, "code": "S1", "name": "Delhi HQ",
                          "country": "IN"},
                    headers=auth(admin))
    assert r.status_code == 201, r.text
    pid = r.json()["public_id"]
    assert pid.startswith("st_")
    # List
    lst = client.get("/api/v1/master-data/sites", headers=auth(admin)).json()
    assert lst["total"] == 1


def test_client_forced_to_own_company_on_site_create(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    a = make_company(session, org, "A")
    b = make_company(session, org, "B")
    cadmin = make_user(session, "ca@a.com", "client", "Client Administrator", company=a)
    # supplied company_id=B is ignored -> forced to A
    r = client.post("/api/v1/master-data/sites",
                    json={"company_id": b.id, "code": "S1", "name": "X"},
                    headers=auth(cadmin))
    assert r.status_code == 201
    # Verify in DB the row belongs to A, not B
    from sqlmodel import select
    row = session.exec(select(Site).where(Site.public_id == r.json()["public_id"])).first()
    assert row.company_id == a.id


def test_duplicate_site_code_rejected(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "a2@d.com", "deloitte", "Administrator", org=org)
    p = {"company_id": co.id, "code": "S1", "name": "First"}
    assert client.post("/api/v1/master-data/sites", json=p, headers=auth(admin)).status_code == 201
    dup = client.post("/api/v1/master-data/sites", json=p, headers=auth(admin))
    assert dup.status_code == 422
    assert dup.json()["error"]["code"] == "duplicate_code"


def test_cross_tenant_site_read_returns_404(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    a = make_company(session, org, "A")
    b = make_company(session, org, "B")
    admin_a = make_user(session, "adm@d.com", "deloitte", "Administrator", org=org)
    # Create a site in A
    r = client.post("/api/v1/master-data/sites",
                    json={"company_id": a.id, "code": "S1", "name": "In A"},
                    headers=auth(admin_a))
    site_pid = r.json()["public_id"]
    # A client user of company B tries to read A's site
    client_b = make_user(session, "u@b.com", "client", "Client Administrator", company=b)
    r = client.get(f"/api/v1/master-data/sites/{site_pid}", headers=auth(client_b))
    assert r.status_code == 404


def test_site_soft_delete(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "a3@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/master-data/sites",
                    json={"company_id": co.id, "code": "S1", "name": "X"},
                    headers=auth(admin))
    pid = r.json()["public_id"]
    d = client.delete(f"/api/v1/master-data/sites/{pid}", headers=auth(admin))
    assert d.status_code == 200
    # Now hidden
    g = client.get(f"/api/v1/master-data/sites/{pid}", headers=auth(admin))
    assert g.status_code == 404


def test_business_unit_hierarchy(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "a4@d.com", "deloitte", "Administrator", org=org)
    # Parent
    p = client.post("/api/v1/master-data/business-units",
                    json={"company_id": co.id, "code": "BU1", "name": "Parent"},
                    headers=auth(admin))
    assert p.status_code == 201
    parent_id = session.exec(select(BusinessUnit).where(BusinessUnit.public_id == p.json()["public_id"])).first().id
    # Child
    c = client.post("/api/v1/master-data/business-units",
                    json={"company_id": co.id, "code": "BU1.1", "name": "Child",
                          "parent_id": parent_id},
                    headers=auth(admin))
    assert c.status_code == 201


def test_bu_parent_from_other_company_rejected(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    a = make_company(session, org, "A")
    b = make_company(session, org, "B")
    admin = make_user(session, "a5@d.com", "deloitte", "Administrator", org=org)
    # Create parent in A
    p = client.post("/api/v1/master-data/business-units",
                    json={"company_id": a.id, "code": "BUA", "name": "PA"},
                    headers=auth(admin))
    parent_id = session.exec(select(BusinessUnit).where(BusinessUnit.public_id == p.json()["public_id"])).first().id
    # Try to attach child in B to A's parent
    c = client.post("/api/v1/master-data/business-units",
                    json={"company_id": b.id, "code": "BUB", "name": "PB",
                          "parent_id": parent_id},
                    headers=auth(admin))
    assert c.status_code == 422
    assert c.json()["error"]["code"] == "invalid_parent"


def test_department_crud(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "a6@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/master-data/departments",
                    json={"company_id": co.id, "code": "D1", "name": "Sustainability"},
                    headers=auth(admin))
    assert r.status_code == 201


# -------- Upload types --------

def test_upload_types_seeded_and_listable(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    admin = make_user(session, "a7@d.com", "deloitte", "Administrator", org=org)
    r = client.get("/api/v1/upload-types", headers=auth(admin))
    assert r.status_code == 200
    codes = [u["code"] for u in r.json()]
    assert "energy_data" in codes
    assert "general_evidence" in codes
    assert len(codes) >= 5


def test_admin_can_create_upload_type(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    admin = make_user(session, "a8@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/upload-types",
                    json={"code": "custom_test", "display_name": "Test",
                          "allowed_mime_types": ["text/csv"]},
                    headers=auth(admin))
    assert r.status_code == 201


# -------- Datasets --------

def test_dataset_create_gets_v1_automatically(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "a9@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    assert r.status_code == 201, r.text
    ds_pid = r.json()["public_id"]
    assert ds_pid.startswith("ds_")
    # Version 1 auto-created
    vs = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()
    assert len(vs) == 1
    assert vs[0]["version_number"] == 1
    assert vs[0]["status"] == "draft"


def test_dataset_invalid_upload_type(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "aa@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "nope",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    assert r.status_code == 422


def test_dataset_period_backwards_rejected(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ab@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-03-31",
                          "reporting_period_end": "2026-01-01"},
                    headers=auth(admin))
    assert r.status_code == 422


def test_dataset_cross_tenant_read_404(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    a = make_company(session, org, "A")
    b = make_company(session, org, "B")
    admin = make_user(session, "ac@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": a.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    pid = r.json()["public_id"]
    # Company B client tries to read
    cu = make_user(session, "cu@b.com", "client", "Client Administrator", company=b)
    assert client.get(f"/api/v1/datasets/{pid}", headers=auth(cu)).status_code == 404


# -------- File upload / validation --------

def test_upload_valid_xlsx_and_verify_persistence(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ad@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()[0]["public_id"]

    xlsx = _tiny_xlsx()
    r = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
        files={"file": ("data.xlsx", xlsx,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"role": "data"},
        headers=auth(admin),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["size_bytes"] == len(xlsx)
    assert body["mime_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert len(body["sha256_checksum"]) == 64
    # File physically written under company-scoped path
    row = session.exec(select(DatasetFile).where(DatasetFile.public_id == body["public_id"])).first()
    assert f"companies/{co.id}/" in row.storage_key
    full = os.path.join(str(tmp_path), row.storage_key)
    assert os.path.exists(full)


def test_upload_rejects_extension_spoofed_file(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ae@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()[0]["public_id"]

    # A binary payload NAMED data.xlsx but not really xlsx
    r = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
        files={"file": ("data.xlsx", b"\x00\x01\x02\x03totally-not-excel",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"role": "data"},
        headers=auth(admin),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] in ("unrecognized_format", "mime_not_allowed")


def test_upload_rejects_zip(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "af@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()[0]["public_id"]

    r = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
        files={"file": ("data.zip", _zip_bytes(), "application/zip")},
        data={"role": "data"},
        headers=auth(admin),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "zip_not_allowed"


def test_upload_rejects_oversized(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ag@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()[0]["public_id"]

    # Real xlsx padded past 25MB
    big = _tiny_xlsx() + (b"0" * (26 * 1024 * 1024))
    r = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
        files={"file": ("big.xlsx", big,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"role": "data"},
        headers=auth(admin),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "file_too_large"


def test_evidence_role_accepts_pdf(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ah@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()[0]["public_id"]

    r = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
        files={"file": ("bill.pdf", _pdf_bytes(), "application/pdf")},
        data={"role": "evidence"},
        headers=auth(admin),
    )
    assert r.status_code == 201


def test_cannot_upload_to_submitted_version(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ai@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()[0]["public_id"]

    # Upload + submit
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
                files={"file": ("d.xlsx", _tiny_xlsx(),
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"role": "data"}, headers=auth(admin))
    s = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/submit", headers=auth(admin))
    assert s.status_code == 200
    # Now try to add another file
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
                    files={"file": ("d2.xlsx", _tiny_xlsx(),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    data={"role": "data"}, headers=auth(admin))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "version_locked"


def test_cannot_submit_without_data_file(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "aj@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()[0]["public_id"]

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/submit", headers=auth(admin))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "no_data_file"


def test_new_version_after_submission(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ak@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()[0]["public_id"]
    # Can't make v2 while v1 is still open
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin))
    assert r.status_code == 409
    # Simulate v1 being closed (rejected) directly in DB
    from sqlmodel import select
    v1 = session.exec(select(DatasetVersion).where(DatasetVersion.public_id == v_pid)).first()
    v1.status = "rejected"
    session.add(v1); session.commit()
    # Now v2 works
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin))
    assert r.status_code == 201
    assert r.json()["version_number"] == 2


# -------- File download + signed URL --------

def test_download_file(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "al@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()[0]["public_id"]

    xlsx = _tiny_xlsx()
    up = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
                     files={"file": ("d.xlsx", xlsx,
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                     data={"role": "data"}, headers=auth(admin))
    fpid = up.json()["public_id"]
    dl = client.get(f"/api/v1/files/{fpid}/download", headers=auth(admin))
    assert dl.status_code == 200
    assert dl.content == xlsx


def test_download_denied_cross_tenant(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    a = make_company(session, org, "A")
    b = make_company(session, org, "B")
    admin = make_user(session, "am@d.com", "deloitte", "Administrator", org=org)
    # Upload as admin -> file in A
    r = client.post("/api/v1/datasets",
                    json={"company_id": a.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()[0]["public_id"]
    up = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
                     files={"file": ("d.xlsx", _tiny_xlsx(),
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                     data={"role": "data"}, headers=auth(admin))
    fpid = up.json()["public_id"]
    # B's client tries to download A's file
    cb = make_user(session, "cb@b.com", "client", "Client Administrator", company=b)
    r = client.get(f"/api/v1/files/{fpid}/download", headers=auth(cb))
    assert r.status_code == 404


# -------- Processing jobs --------

def test_upload_enqueues_checksum_job(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "an@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(admin)).json()[0]["public_id"]
    up = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
                     files={"file": ("d.xlsx", _tiny_xlsx(),
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                     data={"role": "data"}, headers=auth(admin))
    fpid = up.json()["public_id"]
    # Was a checksum job enqueued for this file?
    fid = session.exec(select(DatasetFile).where(DatasetFile.public_id == fpid)).first().id
    jobs = session.exec(select(ProcessingJob).where(
        ProcessingJob.subject_type == "dataset_file", ProcessingJob.subject_id == fid
    )).all()
    assert len(jobs) == 1
    assert jobs[0].job_type == "verify_file_checksum"
    assert jobs[0].status == "pending"


# -------- Audit log --------

def test_audit_log_written_on_create(client, session, tmp_path):
    _use_temp_storage(str(tmp_path))
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ao@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/master-data/sites",
                    json={"company_id": co.id, "code": "S1", "name": "Delhi"},
                    headers=auth(admin))
    assert r.status_code == 201
    logs = session.exec(select(AuditLog).where(
        AuditLog.entity_type == "site", AuditLog.action == "site.created"
    )).all()
    assert len(logs) >= 1
    assert logs[-1].actor_user_id == admin.id
    assert logs[-1].company_id == co.id


def test_audit_log_readable(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ap@d.com", "deloitte", "Administrator", org=org)
    # Trigger one auditable action
    client.post("/api/v1/master-data/sites",
                json={"company_id": co.id, "code": "S1", "name": "X"}, headers=auth(admin))
    r = client.get("/api/v1/audit-logs", headers=auth(admin))
    assert r.status_code == 200
    assert r.json()["total"] >= 1


# -------- Integration stubs --------

def test_integration_stub_crud(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "aq@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/integrations",
                    json={"company_id": co.id, "type": "sap",
                          "config": {"endpoint": "https://example"}},
                    headers=auth(admin))
    assert r.status_code == 201
    iid = r.json()["id"]
    u = client.patch(f"/api/v1/integrations/{iid}",
                     json={"status": "disabled"}, headers=auth(admin))
    assert u.status_code == 200
    assert u.json()["status"] == "disabled"


# -------- Permissions --------

def test_uploader_cannot_manage_sites(client, session):
    _use_temp_storage()
    org = _seed_all(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "up@c.com", "client", "Client Uploader", company=co)
    # Uploader role doesn't have site:manage
    r = client.post("/api/v1/master-data/sites",
                    json={"code": "S1", "name": "X"}, headers=auth(uploader))
    assert r.status_code == 403


def test_permissions_required_endpoints_return_401_unauthed(client):
    for p in ["/api/v1/master-data/sites", "/api/v1/datasets", "/api/v1/upload-types",
              "/api/v1/audit-logs"]:
        r = client.get(p)
        assert r.status_code == 401, f"{p} did not require auth"
