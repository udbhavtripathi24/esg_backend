"""Worker tests.

process_one() opens its own Session(engine) blocks, which is correct for
production (each poll gets a fresh connection). Testing it end-to-end with
SQLite is awkward because the test's session fixture is a DIFFERENT in-memory
SQLite than what process_one would open.

We therefore test:
- Handler registration + direct handler execution (unit test of the logic)
- Full end-to-end (claim -> run -> complete + retry -> dead) is verified
  against real Postgres in the stage-completion report, not here.
"""
import io
from datetime import datetime, date
from sqlmodel import select
import pytest
from tests.conftest_helpers import bootstrap, make_company, make_user
from scripts_seed_upload_types import seed_upload_types
from app.models.dataset import Dataset, DatasetVersion, DatasetFile
from app.models.processing_job import ProcessingJob
from app.models.upload_type import UploadType


def _tiny_xlsx() -> bytes:
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.append(["a"])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _set_storage(tmp_path):
    from app.storage.factory import get_storage as _gs
    _gs.cache_clear()
    from app.core.config import settings
    settings.STORAGE_LOCAL_ROOT = str(tmp_path)
    settings.STORAGE_BACKEND = "local"


def test_handlers_registered():
    from app.workers import _HANDLERS
    assert "verify_file_checksum" in _HANDLERS
    assert "extract_file_metadata" in _HANDLERS


def test_verify_checksum_handler_succeeds_on_valid_file(session, tmp_path):
    """Call the handler directly with a good file — expect no exception."""
    _set_storage(tmp_path)
    org = bootstrap(session)
    seed_upload_types(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "wh@d.com", "deloitte", "Administrator", org=org)
    ut = session.exec(select(UploadType).where(UploadType.code == "energy_data")).first()
    ds = Dataset(company_id=co.id, upload_type_id=ut.id,
                 reporting_period_start=date(2026,1,1),
                 reporting_period_end=date(2026,3,31), created_by=admin.id)
    session.add(ds); session.commit(); session.refresh(ds)
    v = DatasetVersion(dataset_id=ds.id, version_number=1, status="draft",
                       uploaded_by=admin.id)
    session.add(v); session.commit(); session.refresh(v)

    from app.storage import get_storage
    from app.storage.factory import build_storage_key
    xlsx = _tiny_xlsx()
    key = build_storage_key(co.id, ds.public_id, 1, "df_x", "d.xlsx")
    stored = get_storage().put(key, io.BytesIO(xlsx),
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    df = DatasetFile(public_id="df_wt", dataset_version_id=v.id, role="data",
                     storage_key=key, original_filename="d.xlsx",
                     mime_type=stored.mime_type, size_bytes=stored.size_bytes,
                     sha256_checksum=stored.sha256_checksum, uploaded_by=admin.id)
    session.add(df); session.commit(); session.refresh(df)
    job = ProcessingJob(job_type="verify_file_checksum", subject_type="dataset_file",
                        subject_id=df.id)

    from app.workers import _HANDLERS
    # Should complete without raising
    _HANDLERS["verify_file_checksum"](session, job)


def test_verify_checksum_handler_raises_on_missing_file(session, tmp_path):
    _set_storage(tmp_path)
    bootstrap(session)
    job = ProcessingJob(job_type="verify_file_checksum", subject_type="dataset_file",
                        subject_id=999999)
    from app.workers import _HANDLERS
    with pytest.raises(RuntimeError, match="not found"):
        _HANDLERS["verify_file_checksum"](session, job)


def test_verify_checksum_handler_detects_corruption(session, tmp_path):
    """If the stored file is corrupted, the handler must raise 'checksum mismatch'."""
    _set_storage(tmp_path)
    org = bootstrap(session)
    seed_upload_types(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "wc@d.com", "deloitte", "Administrator", org=org)
    ut = session.exec(select(UploadType).where(UploadType.code == "energy_data")).first()
    ds = Dataset(company_id=co.id, upload_type_id=ut.id,
                 reporting_period_start=date(2026,1,1),
                 reporting_period_end=date(2026,3,31), created_by=admin.id)
    session.add(ds); session.commit(); session.refresh(ds)
    v = DatasetVersion(dataset_id=ds.id, version_number=1, status="draft",
                       uploaded_by=admin.id)
    session.add(v); session.commit(); session.refresh(v)

    from app.storage import get_storage
    from app.storage.factory import build_storage_key
    xlsx = _tiny_xlsx()
    key = build_storage_key(co.id, ds.public_id, 1, "df_c", "d.xlsx")
    stored = get_storage().put(key, io.BytesIO(xlsx),
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    df = DatasetFile(public_id="df_corrupt", dataset_version_id=v.id, role="data",
                     storage_key=key, original_filename="d.xlsx",
                     mime_type=stored.mime_type, size_bytes=stored.size_bytes,
                     sha256_checksum="0" * 64,  # WRONG checksum on record
                     uploaded_by=admin.id)
    session.add(df); session.commit(); session.refresh(df)

    from app.workers import _HANDLERS
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _HANDLERS["verify_file_checksum"](session,
                                          ProcessingJob(job_type="verify_file_checksum",
                                                        subject_type="dataset_file",
                                                        subject_id=df.id))
