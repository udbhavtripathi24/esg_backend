"""Stage 5 tests — review workflow, comments, notifications, SoD, threading."""
import io
from datetime import date, datetime
from sqlmodel import select
import pytest

from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from scripts_seed_upload_types import seed_upload_types
from app.models.dataset import Dataset, DatasetVersion, DatasetFile
from app.models.review import Review, ReviewComment
from app.models.notification import Notification, NotificationOutbox
from app.models.processing_job import ProcessingJob
from app.models.upload_type import UploadType
from app.models.audit_log import AuditLog


# ---------- Helpers ----------

def _tiny_xlsx() -> bytes:
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(["a", "b", "c"]); ws.append([1, 2, 3])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _seed(session):
    org = bootstrap(session)
    seed_upload_types(session)
    return org


def _set_storage(tmp_path):
    from app.storage.factory import get_storage as _gs
    _gs.cache_clear()
    from app.core.config import settings
    settings.STORAGE_LOCAL_ROOT = str(tmp_path)
    settings.STORAGE_BACKEND = "local"


def _submitted_version(client, session, admin, co):
    """Create a dataset + upload a file + submit — returns (ds_pid, v_pid)."""
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(admin))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions",
                       headers=auth(admin)).json()[0]["public_id"]
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
                    files={"file": ("d.xlsx", _tiny_xlsx(),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    data={"role": "data"}, headers=auth(admin))
    assert r.status_code == 201, r.text
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/submit",
                    headers=auth(admin))
    assert r.status_code == 200, r.text
    return ds_pid, v_pid


# ---------- Review assignment ----------

def test_assign_reviewer_transitions_to_under_review(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "up@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rv@d.com", "deloitte", "Reviewer", org=org)

    ds_pid, v_pid = _submitted_version(client, session, uploader, co)

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": reviewer.id},
                    headers=auth(uploader))
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending"
    assert r.json()["reviewer_user_id"] == reviewer.id

    # Version should be under_review now
    v = session.exec(select(DatasetVersion).where(
        DatasetVersion.public_id == v_pid)).first()
    session.refresh(v)
    assert v.status == "under_review"


def test_assign_reviewer_idempotent(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "up2@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rv2@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)

    r1 = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                     json={"reviewer_user_id": reviewer.id},
                     headers=auth(uploader))
    r2 = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                     json={"reviewer_user_id": reviewer.id},
                     headers=auth(uploader))
    assert r1.json()["public_id"] == r2.json()["public_id"]


def test_cannot_assign_to_draft_version(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "up3@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rv3@d.com", "deloitte", "Reviewer", org=org)
    # Draft (not submitted)
    r = client.post("/api/v1/datasets",
                    json={"company_id": co.id, "upload_type_code": "energy_data",
                          "reporting_period_start": "2026-01-01",
                          "reporting_period_end": "2026-03-31"},
                    headers=auth(uploader))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions",
                       headers=auth(uploader)).json()[0]["public_id"]
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": reviewer.id},
                    headers=auth(uploader))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_state"


def test_cannot_assign_cross_org_reviewer(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    # A second org (a rival Deloitte instance for the test)
    from app.models.organization import Organization
    org2 = Organization(name="OtherOrg"); session.add(org2); session.commit(); session.refresh(org2)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "up4@d.com", "deloitte", "Administrator", org=org)
    foreign_reviewer = make_user(session, "rv4@x.com", "deloitte", "Reviewer", org=org2)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": foreign_reviewer.id},
                    headers=auth(uploader))
    assert r.status_code == 422
    assert "same organization" in r.json()["error"]["message"]


# ---------- Decision recording ----------

def test_approve_full_transactional_flow(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "u5@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "r5@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv_pid = r.json()["public_id"]

    r = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
        json={"decision": "approved", "note": "Looks good."},
        headers=auth(reviewer),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert r.json()["decided_at"] is not None

    # Version + dataset advanced
    v = session.exec(select(DatasetVersion).where(
        DatasetVersion.public_id == v_pid)).first()
    ds = session.exec(select(Dataset).where(Dataset.public_id == ds_pid)).first()
    session.refresh(v); session.refresh(ds)
    assert v.status == "approved"
    assert ds.status == "approved"
    assert v.review_decision_summary["status"] == "approved"

    # Audit entry
    audits = session.exec(select(AuditLog).where(
        AuditLog.action == "review.approved")).all()
    assert len(audits) == 1

    # KPI recalc job enqueued
    jobs = session.exec(select(ProcessingJob).where(
        ProcessingJob.job_type == "recalculate_kpi_values")).all()
    assert len(jobs) == 1
    assert jobs[0].subject_id == v.id

    # Notification outbox has an approval event
    ob = session.exec(select(NotificationOutbox).where(
        NotificationOutbox.event_type == "data_approved")).all()
    assert len(ob) == 1


def test_changes_requested_does_not_auto_create_new_version(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "u6@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "r6@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv_pid = r.json()["public_id"]
    client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
        json={"decision": "changes_requested", "note": "Please fix row 14."},
        headers=auth(reviewer),
    )
    # Only v1 should exist
    versions = client.get(f"/api/v1/datasets/{ds_pid}/versions",
                          headers=auth(uploader)).json()
    assert len(versions) == 1
    assert versions[0]["status"] == "changes_requested"

    # Client MANUALLY creates v2 (this comes from Stage 4)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions",
                    headers=auth(uploader))
    assert r.status_code == 201
    assert r.json()["version_number"] == 2
    assert r.json()["status"] == "draft"


def test_rejected_is_terminal_no_new_version_auto(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "u7@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "r7@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv_pid = r.json()["public_id"]
    client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
        json={"decision": "rejected", "note": "Wrong dataset entirely."},
        headers=auth(reviewer),
    )

    ds = session.exec(select(Dataset).where(Dataset.public_id == ds_pid)).first()
    session.refresh(ds)
    assert ds.status == "rejected"
    # No KPI recalc for rejection
    jobs = session.exec(select(ProcessingJob).where(
        ProcessingJob.job_type == "recalculate_kpi_values")).all()
    assert len(jobs) == 0


def test_note_required(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "u8@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "r8@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv_pid = r.json()["public_id"]

    # Empty note -> 422 from pydantic min_length
    r = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
        json={"decision": "approved", "note": ""},
        headers=auth(reviewer),
    )
    assert r.status_code == 422


def test_invalid_decision(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "u9@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "r9@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv_pid = r.json()["public_id"]
    r = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
        json={"decision": "maybe", "note": "unsure"},
        headers=auth(reviewer),
    )
    assert r.status_code == 422


def test_non_assigned_reviewer_cannot_decide(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "ua@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "ra@d.com", "deloitte", "Reviewer", org=org)
    outsider = make_user(session, "out@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv_pid = r.json()["public_id"]

    r = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
        json={"decision": "approved", "note": "hi"},
        headers=auth(outsider),
    )
    # 404 (not 403) — non-assigned reviewer sees no existence leak
    assert r.status_code == 404


def test_double_decision_rejected(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "ub@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rb@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv_pid = r.json()["public_id"]
    r1 = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
        json={"decision": "approved", "note": "yes"},
        headers=auth(reviewer),
    )
    assert r1.status_code == 200
    r2 = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
        json={"decision": "rejected", "note": "no"},
        headers=auth(reviewer),
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "already_decided"


# ---------- Segregation of duties ----------

def test_sod_blocks_uploader_from_approving_own_version(client, session, tmp_path):
    """When enforce_upload_approval_segregation is true (default), the same user
    cannot both upload and approve."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    # Same user is uploader AND reviewer (has both roles)
    solo = make_user(session, "solo@d.com", "deloitte", "Administrator", org=org)
    ds_pid, v_pid = _submitted_version(client, session, solo, co)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": solo.id}, headers=auth(solo))
    rv_pid = r.json()["public_id"]
    r = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
        json={"decision": "approved", "note": "self-approve attempt"},
        headers=auth(solo),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "segregation_of_duties"


def test_sod_can_be_disabled_per_company(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    co.enforce_upload_approval_segregation = False
    session.add(co); session.commit()

    solo = make_user(session, "sod@d.com", "deloitte", "Administrator", org=org)
    ds_pid, v_pid = _submitted_version(client, session, solo, co)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": solo.id}, headers=auth(solo))
    rv_pid = r.json()["public_id"]
    r = client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
        json={"decision": "approved", "note": "SoD disabled, self-approve ok"},
        headers=auth(solo),
    )
    assert r.status_code == 200


# ---------- Comments + threading ----------

def test_root_comment_and_reply(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "uc@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rc@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)

    # Assign reviewer first — commenting requires access to the version
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))

    # Reviewer needs comment:create — Reviewer role has it in seed
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/comments",
                    json={"body": "First observation."}, headers=auth(reviewer))
    assert r.status_code == 201, r.text
    root = r.json()
    assert root["parent_comment_id"] is None

    parent_id = session.exec(select(ReviewComment).where(
        ReviewComment.public_id == root["public_id"])).first().id

    r2 = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/comments",
                     json={"body": "Following up.", "parent_comment_id": parent_id},
                     headers=auth(uploader))
    assert r2.status_code == 201
    assert r2.json()["parent_comment_id"] == parent_id


def test_reply_depth_capped(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    u = make_user(session, "ud@d.com", "deloitte", "Administrator", org=org)
    ds_pid, v_pid = _submitted_version(client, session, u, co)

    parent_pid = None
    parent_id = None
    for depth in range(1, 5):  # try depths 1..4; the cap is 3
        payload = {"body": f"depth {depth}"}
        if parent_id is not None:
            payload["parent_comment_id"] = parent_id
        r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/comments",
                        json=payload, headers=auth(u))
        if depth <= 3:
            assert r.status_code == 201, (depth, r.text)
            parent_pid = r.json()["public_id"]
            parent_id = session.exec(select(ReviewComment).where(
                ReviewComment.public_id == parent_pid)).first().id
        else:
            assert r.status_code == 422
            assert r.json()["error"]["code"] == "thread_too_deep"


def test_reply_to_parent_from_different_version_rejected(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    u = make_user(session, "ue@d.com", "deloitte", "Administrator", org=org)
    ds1_pid, v1_pid = _submitted_version(client, session, u, co)
    ds2_pid, v2_pid = _submitted_version(client, session, u, co)

    # Comment on version 1
    r1 = client.post(f"/api/v1/datasets/{ds1_pid}/versions/{v1_pid}/comments",
                     json={"body": "on v1"}, headers=auth(u))
    parent_id = session.exec(select(ReviewComment).where(
        ReviewComment.public_id == r1.json()["public_id"])).first().id

    # Try to reply on version 2 pointing at version 1's comment
    r2 = client.post(f"/api/v1/datasets/{ds2_pid}/versions/{v2_pid}/comments",
                     json={"body": "cross", "parent_comment_id": parent_id},
                     headers=auth(u))
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "invalid_parent"


def test_comments_list_by_version(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    u = make_user(session, "uf@d.com", "deloitte", "Administrator", org=org)
    ds_pid, v_pid = _submitted_version(client, session, u, co)
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/comments",
                json={"body": "one"}, headers=auth(u))
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/comments",
                json={"body": "two"}, headers=auth(u))
    r = client.get(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/comments",
                   headers=auth(u))
    assert r.status_code == 200
    assert len(r.json()) == 2


# ---------- Notifications ----------

def test_notification_created_for_reviewer_on_assignment(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "un1@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rn1@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    # Fetch notifications AS THE REVIEWER — this triggers outbox dispatch
    r = client.get("/api/v1/notifications", headers=auth(reviewer))
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(n["event_type"] == "new_assignment" for n in items)


def test_notification_on_approval_goes_to_uploader(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "un2@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rn2@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv_pid = r.json()["public_id"]
    client.post(
        f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
        json={"decision": "approved", "note": "ok"},
        headers=auth(reviewer),
    )
    r = client.get("/api/v1/notifications", headers=auth(uploader))
    events = [n["event_type"] for n in r.json()["items"]]
    assert "data_approved" in events


def test_mark_read_and_mark_all_read(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "un3@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rn3@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    # Get + dispatch
    r = client.get("/api/v1/notifications", headers=auth(reviewer))
    nid = r.json()["items"][0]["id"]
    r = client.post(f"/api/v1/notifications/{nid}/mark-read",
                    headers=auth(reviewer))
    assert r.status_code == 200
    assert r.json()["is_read"] is True

    # Trigger another notification
    ds2, v2 = _submitted_version(client, session, uploader, co)
    client.post(f"/api/v1/datasets/{ds2}/versions/{v2}/reviews",
                json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    client.get("/api/v1/notifications", headers=auth(reviewer))  # dispatch

    r = client.post("/api/v1/notifications/mark-all-read", headers=auth(reviewer))
    assert r.status_code == 200
    r = client.get("/api/v1/notifications?unread_only=true", headers=auth(reviewer))
    assert r.json()["total"] == 0


def test_cannot_mark_another_users_notification(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "un4@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rn4@d.com", "deloitte", "Reviewer", org=org)
    other = make_user(session, "ot@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, uploader, co)
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    r = client.get("/api/v1/notifications", headers=auth(reviewer))
    nid = r.json()["items"][0]["id"]
    # Other user tries to mark reviewer's notification -> 404 (no leak)
    r = client.post(f"/api/v1/notifications/{nid}/mark-read", headers=auth(other))
    assert r.status_code == 404


# ---------- Cross-tenant ----------

def test_cross_tenant_review_404(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    a = make_company(session, org, "A")
    b = make_company(session, org, "B")
    admin = make_user(session, "ct@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "ctr@d.com", "deloitte", "Reviewer", org=org)
    ds_pid, v_pid = _submitted_version(client, session, admin, a)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                    json={"reviewer_user_id": reviewer.id}, headers=auth(admin))
    assert r.status_code == 201
    # Client user of company B tries to view A's reviews
    cb = make_user(session, "cb@b.com", "client", "Client Administrator", company=b)
    r = client.get(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews",
                   headers=auth(cb))
    assert r.status_code == 404


# ---------- Auth ----------

def test_endpoints_require_auth(client):
    for p in ["/api/v1/notifications"]:
        r = client.get(p)
        assert r.status_code == 401
