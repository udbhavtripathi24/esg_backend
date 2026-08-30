"""Stage 5 end-to-end verification: full review workflow through real HTTP.

Runs:
  1. Bootstrap two users (uploader + reviewer)
  2. Login both, get tokens
  3. Upload + submit a dataset
  4. Assign reviewer -> verify status transition to under_review
  5. Reviewer adds a comment + a threaded reply
  6. Reviewer approves with note
  7. Verify: dataset status = approved, version status = approved,
     audit entry exists, KPI recalc job enqueued, uploader has notification
  8. New scenario: submit v1, assign, request changes, verify uploader notified,
     then manually create v2 (does NOT auto-create)
  9. SoD scenario: uploader tries to review own version -> 403
 10. Mark notifications as read
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
    ws.append(["site", "period", "qty"]); ws.append(["S1", "2026-Q1", 100])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def step(name, ok, detail=""):
    icon = "✓" if ok else "✗"
    print(f"  {icon} {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        sys.exit(1)


def login(email, password):
    r = requests.post(f"{BASE}/auth/login",
                      data={"username": email, "password": password})
    assert r.status_code == 200, f"login failed: {r.text}"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def main():
    engine = create_engine(DB)
    from app.models.organization import Organization
    from app.models.company import Company
    from app.models.user import User
    from app.models.rbac import Role, UserRole
    from app.core.security import hash_password

    with Session(engine) as s:
        org = s.exec(select(Organization).where(Organization.name == "Deloitte")).first()
        if not org:
            org = Organization(name="Deloitte")
            s.add(org); s.commit(); s.refresh(org)

        # Uploader (also Administrator so can access all data)
        up = s.exec(select(User).where(User.email == "s5up@d.com")).first()
        if not up:
            up = User(name="Uploader", email="s5up@d.com", portal_type="deloitte",
                     role="Administrator", organization_id=org.id,
                     hashed_password=hash_password("password123"))
            s.add(up); s.commit(); s.refresh(up)
            role = s.exec(select(Role).where(Role.code == "Administrator")).first()
            s.add(UserRole(user_id=up.id, role_id=role.id)); s.commit()

        # Reviewer (Reviewer role only)
        rv = s.exec(select(User).where(User.email == "s5rv@d.com")).first()
        if not rv:
            rv = User(name="Reviewer", email="s5rv@d.com", portal_type="deloitte",
                     role="Reviewer", organization_id=org.id,
                     hashed_password=hash_password("password123"))
            s.add(rv); s.commit(); s.refresh(rv)
            role = s.exec(select(Role).where(Role.code == "Reviewer")).first()
            s.add(UserRole(user_id=rv.id, role_id=role.id)); s.commit()

        co = s.exec(select(Company).where(Company.name == "S5 Test Co")).first()
        if not co:
            co = Company(name="S5 Test Co", organization_id=org.id, status="Approved")
            s.add(co); s.commit(); s.refresh(co)
        co_id = co.id
        rv_id = rv.id

    HUP = login("s5up@d.com", "password123")
    HRV = login("s5rv@d.com", "password123")
    step("Both users logged in", True)

    # 3. Create+upload+submit a dataset
    r = requests.post(f"{BASE}/datasets",
                      json={"company_id": co_id, "upload_type_code": "energy_data",
                            "reporting_period_start": "2026-01-01",
                            "reporting_period_end": "2026-03-31"},
                      headers=HUP)
    step("Create dataset", r.status_code == 201, f"HTTP {r.status_code}")
    ds_pid = r.json()["public_id"]
    v_pid = requests.get(f"{BASE}/datasets/{ds_pid}/versions", headers=HUP).json()[0]["public_id"]

    r = requests.post(
        f"{BASE}/datasets/{ds_pid}/versions/{v_pid}/files",
        files={"file": ("e.xlsx", _tiny_xlsx(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"role": "data"}, headers=HUP,
    )
    step("Upload data file", r.status_code == 201)

    r = requests.post(f"{BASE}/datasets/{ds_pid}/versions/{v_pid}/submit", headers=HUP)
    step("Submit for review", r.status_code == 200 and r.json()["status"] == "submitted")

    # 4. Assign reviewer
    r = requests.post(f"{BASE}/datasets/{ds_pid}/versions/{v_pid}/reviews",
                      json={"reviewer_user_id": rv_id}, headers=HUP)
    step("Assign reviewer", r.status_code == 201, f"HTTP {r.status_code} {r.text[:100]}")
    rv_public = r.json()["public_id"]

    # Version should now be under_review
    r = requests.get(f"{BASE}/datasets/{ds_pid}/versions", headers=HUP).json()
    step("Version transitions to under_review",
         r[0]["status"] == "under_review", f"status={r[0]['status']}")

    # 5. Reviewer comments (root + reply)
    r = requests.post(f"{BASE}/datasets/{ds_pid}/versions/{v_pid}/comments",
                      json={"body": "Question: is site S1 in-scope?"}, headers=HRV)
    step("Reviewer adds root comment", r.status_code == 201)
    from sqlmodel import select as _sel
    with Session(engine) as s:
        from app.models.review import ReviewComment
        parent_id = s.exec(_sel(ReviewComment).where(
            ReviewComment.public_id == r.json()["public_id"])).first().id

    r = requests.post(f"{BASE}/datasets/{ds_pid}/versions/{v_pid}/comments",
                      json={"body": "Yes, S1 is in-scope for Q1.",
                            "parent_comment_id": parent_id}, headers=HUP)
    step("Uploader replies to comment",
         r.status_code == 201 and r.json()["parent_comment_id"] == parent_id)

    # 6. Approve
    r = requests.post(
        f"{BASE}/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_public}/decide",
        json={"decision": "approved",
              "note": "Data checks out — site totals match Q1 utility bills."},
        headers=HRV,
    )
    step("Approve decision", r.status_code == 200, f"HTTP {r.status_code} {r.text[:200]}")

    # 7. Verify all downstream effects
    r = requests.get(f"{BASE}/datasets/{ds_pid}", headers=HUP).json()
    step("Dataset status = approved", r["status"] == "approved", f"status={r['status']}")

    # Audit entry
    r = requests.get(f"{BASE}/audit-logs?company_id={co_id}", headers=HUP).json()
    actions = [a["action"] for a in r["items"]]
    step("Audit contains review.assigned", "review.assigned" in actions)
    step("Audit contains review.approved", "review.approved" in actions)

    # KPI recalc job
    with Session(engine) as s:
        from app.models.processing_job import ProcessingJob
        jobs = s.exec(_sel(ProcessingJob).where(
            ProcessingJob.job_type == "recalculate_kpi_values"
        )).all()
    step("KPI recalc job enqueued", len(jobs) >= 1, f"{len(jobs)} jobs")

    # Uploader received a notification
    r = requests.get(f"{BASE}/notifications", headers=HUP).json()
    events = [n["event_type"] for n in r["items"]]
    step("Uploader has data_approved notification", "data_approved" in events)

    # Reviewer had new_assignment
    r = requests.get(f"{BASE}/notifications", headers=HRV).json()
    events = [n["event_type"] for n in r["items"]]
    step("Reviewer has new_assignment notification", "new_assignment" in events)

    # 8. Changes-requested flow does NOT auto-create v2
    r = requests.post(f"{BASE}/datasets",
                      json={"company_id": co_id, "upload_type_code": "water_data",
                            "reporting_period_start": "2026-01-01",
                            "reporting_period_end": "2026-03-31"},
                      headers=HUP)
    ds2 = r.json()["public_id"]
    v2 = requests.get(f"{BASE}/datasets/{ds2}/versions", headers=HUP).json()[0]["public_id"]
    requests.post(f"{BASE}/datasets/{ds2}/versions/{v2}/files",
                  files={"file": ("w.xlsx", _tiny_xlsx(),
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                  data={"role": "data"}, headers=HUP)
    requests.post(f"{BASE}/datasets/{ds2}/versions/{v2}/submit", headers=HUP)
    r = requests.post(f"{BASE}/datasets/{ds2}/versions/{v2}/reviews",
                      json={"reviewer_user_id": rv_id}, headers=HUP)
    rv2 = r.json()["public_id"]
    r = requests.post(
        f"{BASE}/datasets/{ds2}/versions/{v2}/reviews/{rv2}/decide",
        json={"decision": "changes_requested",
              "note": "Please add water source ID column."},
        headers=HRV,
    )
    step("Changes requested", r.status_code == 200)
    versions = requests.get(f"{BASE}/datasets/{ds2}/versions", headers=HUP).json()
    step("Only v1 exists (no auto v2)", len(versions) == 1)

    # 9. Manual v2 creation succeeds
    r = requests.post(f"{BASE}/datasets/{ds2}/versions", headers=HUP)
    step("Manual v2 creation works", r.status_code == 201 and r.json()["version_number"] == 2)

    # 10. SoD: uploader tries to review + approve own version
    r = requests.post(f"{BASE}/datasets",
                      json={"company_id": co_id, "upload_type_code": "waste_data",
                            "reporting_period_start": "2026-01-01",
                            "reporting_period_end": "2026-03-31"},
                      headers=HUP)
    ds3 = r.json()["public_id"]
    v3 = requests.get(f"{BASE}/datasets/{ds3}/versions", headers=HUP).json()[0]["public_id"]
    requests.post(f"{BASE}/datasets/{ds3}/versions/{v3}/files",
                  files={"file": ("wa.xlsx", _tiny_xlsx(),
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                  data={"role": "data"}, headers=HUP)
    requests.post(f"{BASE}/datasets/{ds3}/versions/{v3}/submit", headers=HUP)
    # Uploader assigns themselves
    with Session(engine) as s:
        upid = s.exec(_sel(User).where(User.email == "s5up@d.com")).first().id
    r = requests.post(f"{BASE}/datasets/{ds3}/versions/{v3}/reviews",
                      json={"reviewer_user_id": upid}, headers=HUP)
    rv3 = r.json()["public_id"]
    r = requests.post(
        f"{BASE}/datasets/{ds3}/versions/{v3}/reviews/{rv3}/decide",
        json={"decision": "approved", "note": "self-approve"},
        headers=HUP,
    )
    step("SoD blocks self-approval",
         r.status_code == 403 and r.json()["error"]["code"] == "segregation_of_duties",
         f"HTTP {r.status_code}")

    # 11. Mark-all-read works. First GET (which dispatches outbox), then mark, then verify.
    requests.get(f"{BASE}/notifications", headers=HRV)  # dispatch any pending outbox
    r = requests.post(f"{BASE}/notifications/mark-all-read", headers=HRV)
    step("Mark all read", r.status_code == 200)
    r = requests.get(f"{BASE}/notifications?unread_only=true", headers=HRV).json()
    step("Zero unread after mark-all-read", r["total"] == 0, f"unread={r['total']}")

    print()
    print("ALL STAGE 5 END-TO-END CHECKS PASSED")


if __name__ == "__main__":
    main()
