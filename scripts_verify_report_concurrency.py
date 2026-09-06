"""Real PostgreSQL concurrency verification for Report Generation.

NOT a pytest test -- this project's automated suite always runs against
an in-memory SQLite engine (see tests/conftest.py), which cannot
exercise genuine concurrent access to a real database. This script must
be run against the actual running server (real uvicorn process) backed
by real PostgreSQL, with true concurrent HTTP requests via threading.

Proves the mandatory requirement: UNIQUE(report_id, version_number)
correctly serializes concurrent regeneration attempts -- exactly one
request succeeds (201), the other receives a clean conflict (409), and
the database ends up with exactly the expected version numbers, no
duplicates, no gaps.

Usage (with the real server already running against real Postgres):
    python scripts_verify_report_concurrency.py
"""
import sys
import threading
import requests
from sqlmodel import Session, select
from app.db.session import engine
from app.models.report import Report, ReportVersion

BASE = "http://localhost:8000/api/v1"


def main():
    token = requests.post(f"{BASE}/auth/login", data={
        "username": "audit.admin@d.local", "password": "AuditPass123!",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = requests.post(f"{BASE}/reports", json={
        "reporting_period_start": "2027-01-01", "reporting_period_end": "2027-03-31", "company_id": 1,
    }, headers=headers)
    if r.status_code != 201:
        print(f"FAIL: could not create report -- {r.status_code} {r.text}")
        sys.exit(1)
    report = r.json()
    report_pid, version_pid = report["public_id"], report["current_version"]["public_id"]
    print(f"Created report {report_pid}, version 1: {version_pid}")

    results = []

    def fire_regenerate():
        resp = requests.post(f"{BASE}/reports/{report_pid}/versions/{version_pid}/regenerate", headers=headers)
        results.append((resp.status_code, resp.json()))

    threads = [threading.Thread(target=fire_regenerate) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print("\n=== Concurrent regenerate results ===")
    for status, body in results:
        print(status, body)

    statuses = sorted(s for s, _ in results)
    if statuses != [201, 409]:
        print(f"\nFAIL: expected exactly [201, 409], got {statuses}")
        sys.exit(1)
    print("\nPASS: exactly one request succeeded (201), the other got a clean conflict (409)")

    with Session(engine) as session:
        report_row = session.exec(select(Report).where(Report.public_id == report_pid)).first()
        versions = session.exec(select(ReportVersion).where(ReportVersion.report_id == report_row.id)).all()
        version_numbers = sorted(v.version_number for v in versions)
        print(f"\nActual version_numbers in database: {version_numbers}")
        if version_numbers != [1, 2] or len(version_numbers) != len(set(version_numbers)):
            print("FAIL: duplicate or missing version numbers detected")
            sys.exit(1)
        print("PASS: database contains exactly versions [1, 2], no duplicates, no gaps")


if __name__ == "__main__":
    main()
