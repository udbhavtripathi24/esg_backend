"""End-to-end Stage 4 verification through the real HTTP API + Postgres.

Runs a full journey:
  1. Bootstrap org/company/admin
  2. Login (get JWT)
  3. Create site
  4. Create dataset
  5. Upload xlsx file
  6. Submit version
  7. Download file back
  8. Verify audit logs exist
  9. Verify processing job was enqueued

Prints PASS/FAIL for each step. Exits nonzero on any failure.
"""
import io
import sys
import time
import requests
from sqlmodel import Session, create_engine, select

BASE = "http://127.0.0.1:8888/api/v1"
DB = "postgresql+psycopg2://esg:esg@localhost:5432/esg_platform"


def _tiny_xlsx() -> bytes:
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(["site_code", "period", "quantity", "unit"])
    ws.append(["S1", "2026-Q1", 100.0, "MWh"])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def step(name, ok, detail=""):
    icon = "✓" if ok else "✗"
    print(f"  {icon} {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        sys.exit(1)


def main():
    # Direct DB setup — we don't have a signup endpoint yet, so we bootstrap
    # a superuser via SQL directly (this simulates the seed-time onboarding).
    engine = create_engine(DB)
    from app.models.organization import Organization
    from app.models.company import Company
    from app.models.user import User
    from app.models.rbac import Role, UserRole
    from app.core.security import hash_password

    with Session(engine) as s:
        # Clean prior test data
        s.exec(select(User).where(User.email == "e2e@d.com")).all()  # touch
        # (skip clean for idempotency — bootstrap only if missing)
        org = s.exec(select(Organization).where(Organization.name == "Deloitte")).first()
        if not org:
            org = Organization(name="Deloitte")
            s.add(org); s.commit(); s.refresh(org)
        u = s.exec(select(User).where(User.email == "e2e@d.com")).first()
        if not u:
            u = User(name="E2E Admin", email="e2e@d.com", portal_type="deloitte",
                    role="Administrator", organization_id=org.id,
                    hashed_password=hash_password("password123"))
            s.add(u); s.commit(); s.refresh(u)
            admin_role = s.exec(select(Role).where(Role.code == "Administrator")).first()
            s.add(UserRole(user_id=u.id, role_id=admin_role.id))
            s.commit()
        co = s.exec(select(Company).where(Company.name == "E2E Test Co")).first()
        if not co:
            co = Company(name="E2E Test Co", organization_id=org.id, status="Approved")
            s.add(co); s.commit(); s.refresh(co)
        co_id = co.id

    # 1. Login
    r = requests.post(f"{BASE}/auth/login",
                      data={"username": "e2e@d.com", "password": "password123"})
    step("Login", r.status_code == 200, f"HTTP {r.status_code}")
    tok = r.json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    # 2. Create site
    r = requests.post(f"{BASE}/master-data/sites",
                      json={"company_id": co_id, "code": f"SITE_E2E_{int(time.time())}",
                            "name": "E2E Site", "country": "IN"},
                      headers=H)
    step("Create site", r.status_code == 201, f"HTTP {r.status_code} {r.text[:100]}")
    site_pid = r.json()["public_id"]
    step("Site has public_id with prefix", site_pid.startswith("st_"), site_pid)

    # 3. Create dataset
    r = requests.post(f"{BASE}/datasets",
                      json={"company_id": co_id,
                            "site_public_id": site_pid,
                            "upload_type_code": "energy_data",
                            "reporting_period_start": "2026-01-01",
                            "reporting_period_end": "2026-03-31"},
                      headers=H)
    step("Create dataset", r.status_code == 201, f"HTTP {r.status_code}")
    ds_pid = r.json()["public_id"]

    # 4. Get v1 (auto-created)
    r = requests.get(f"{BASE}/datasets/{ds_pid}/versions", headers=H)
    step("v1 auto-created", r.status_code == 200 and len(r.json()) == 1
                            and r.json()[0]["version_number"] == 1)
    v_pid = r.json()[0]["public_id"]

    # 5. Upload real xlsx
    xlsx = _tiny_xlsx()
    r = requests.post(
        f"{BASE}/datasets/{ds_pid}/versions/{v_pid}/files",
        files={"file": ("energy.xlsx", xlsx,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"role": "data"}, headers=H,
    )
    step("Upload xlsx", r.status_code == 201, f"HTTP {r.status_code} {r.text[:150]}")
    fpid = r.json()["public_id"]
    checksum = r.json()["sha256_checksum"]

    # 6. Reject an extension-spoofed file
    r = requests.post(
        f"{BASE}/datasets/{ds_pid}/versions/{v_pid}/files",
        files={"file": ("evil.xlsx", b"not really xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"role": "data"}, headers=H,
    )
    step("Extension-spoofed file rejected", r.status_code == 400,
         f"HTTP {r.status_code} error={r.json().get('error', {}).get('code')}")

    # 7. Submit
    r = requests.post(f"{BASE}/datasets/{ds_pid}/versions/{v_pid}/submit", headers=H)
    step("Submit version", r.status_code == 200 and r.json()["status"] == "submitted",
         f"HTTP {r.status_code}")

    # 8. Cannot add file after submission
    r = requests.post(
        f"{BASE}/datasets/{ds_pid}/versions/{v_pid}/files",
        files={"file": ("another.xlsx", xlsx,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"role": "data"}, headers=H,
    )
    step("Cannot add file after submission", r.status_code == 409, f"HTTP {r.status_code}")

    # 9. Download the file back
    r = requests.get(f"{BASE}/files/{fpid}/download", headers=H)
    step("Download file", r.status_code == 200 and len(r.content) == len(xlsx),
         f"HTTP {r.status_code}, {len(r.content)} bytes")

    import hashlib
    step("Downloaded content matches checksum",
         hashlib.sha256(r.content).hexdigest() == checksum)

    # 10. Audit log exists for the operations
    r = requests.get(f"{BASE}/audit-logs?company_id={co_id}", headers=H)
    step("Audit log accessible", r.status_code == 200)
    actions = [row["action"] for row in r.json()["items"]]
    step("Audit records site.created", "site.created" in actions,
         f"actions: {actions[:5]}")
    step("Audit records dataset.created", "dataset.created" in actions)
    step("Audit records file.uploaded", "file.uploaded" in actions)
    step("Audit records dataset_version.submitted",
         "dataset_version.submitted" in actions)

    # 11. Processing job was enqueued for the file
    with Session(engine) as s:
        from app.models.dataset import DatasetFile
        from app.models.processing_job import ProcessingJob
        f_row = s.exec(select(DatasetFile).where(DatasetFile.public_id == fpid)).first()
        jobs = s.exec(select(ProcessingJob).where(
            ProcessingJob.subject_type == "dataset_file",
            ProcessingJob.subject_id == f_row.id,
        )).all()
    step("Checksum job enqueued", len(jobs) == 1 and
         jobs[0].job_type == "verify_file_checksum",
         f"{len(jobs)} jobs")

    # 12. Run one worker pass and verify job completes
    from app.workers import process_one
    processed = process_one()
    step("Worker processed a job", processed is True)
    with Session(engine) as s:
        from app.models.processing_job import ProcessingJob
        j = s.get(ProcessingJob, jobs[0].id); s.refresh(j)
    step("Job completed", j.status == "completed",
         f"status={j.status}, err={j.last_error[:100] if j.last_error else ''}")

    print()
    print("ALL STAGE 4 END-TO-END CHECKS PASSED")


if __name__ == "__main__":
    main()
