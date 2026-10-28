"""Evidence files behind BRSR answers (see app/models/evidence.py).

Two ways in:
- a preparer attaches evidence to a question or section while it is still
  editable (draft or changes requested);
- the client answers an assurance information request
  (app/assurance/service.py calls ``store_evidence`` with the request).

Files are type-checked by content (magic bytes), not by extension, limited
in size, stored under a company-scoped key with their SHA-256, and served
only through short-lived signed URLs.
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.brsr.question_bank import QUESTIONS_BY_ID, SECTION_CODES
from app.core.errors import AppError, NotFoundError
from app.models.brsr import EDITABLE_STATUSES, BrsrResponseSet
from app.models.evidence import EvidenceFile
from app.models.user import User
from app.services.audit import log_action
from app.services.file_validation import detect_mime_from_bytes
from app.storage import get_storage

MAX_EVIDENCE_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_UPLOAD_TARGET = 50

ALLOWED_MIME = {
    "application/pdf": "PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel",
    "application/vnd.ms-excel": "Excel (97-2003)",
    "text/csv": "CSV",
    "image/png": "PNG image",
    "image/jpeg": "JPEG image",
}
ALLOWED_LABEL = "PDF, Word, Excel, CSV, PNG or JPEG"
EXTENSIONS = {
    "application/pdf": {"pdf"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {"docx"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"xlsx"},
    "application/vnd.ms-excel": {"xls"},
    "text/csv": {"csv"},
    "image/png": {"png"},
    "image/jpeg": {"jpg", "jpeg"},
}


def detect_evidence_mime(filename: str, content: bytes) -> Optional[str]:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    # The whole file is passed, so the Office (zip) check reads the real
    # central directory instead of guessing from the extension.
    return detect_mime_from_bytes(content, filename)


def display_filename(name: str) -> str:
    """A filename safe to store and to put in a Content-Disposition header."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFKC", name)
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '"<>|*?:')
    name = name.strip(" .") or "evidence"
    if len(name) > 150:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 150 - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else name[:150]
    return name


def _storage_key(rs: BrsrResponseSet, ev_public_id: str, filename: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:100]
    return f"companies/{rs.company_id}/brsr/{rs.public_id}/evidence/{ev_public_id}_{safe}"


def validate_target(section_code: str, question_id: Optional[str]) -> None:
    if section_code not in SECTION_CODES:
        raise AppError("invalid_section", f"Unknown BRSR section {section_code}", 422, "section_code")
    if question_id:
        q = QUESTIONS_BY_ID.get(question_id)
        if not q or q["section"] != section_code:
            raise AppError("invalid_question", f"{question_id} is not a question in section {section_code}", 422,
                           "question_id")


def store_evidence(session: Session, actor: User, rs: BrsrResponseSet, *, section_code: str,
                   question_id: Optional[str], filename: str, content: bytes,
                   description: Optional[str] = None, assurance_request_id: Optional[int] = None,
                   ip_address: Optional[str] = None) -> EvidenceFile:
    """Validate and store one file. Does not commit."""
    validate_target(section_code, question_id)
    if not content:
        raise AppError("empty_file", "The file is empty", 400, "file")
    if len(content) > MAX_EVIDENCE_BYTES:
        raise AppError("file_too_large", f"Evidence files are limited to {MAX_EVIDENCE_BYTES // 1024 // 1024} MB",
                       400, "file")
    mime = detect_evidence_mime(filename, content)
    if mime not in ALLOWED_MIME:
        raise AppError("file_type_not_allowed",
                       f"This file type is not accepted as evidence. Use {ALLOWED_LABEL}.", 400, "file")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in EXTENSIONS[mime]:
        raise AppError("file_type_mismatch",
                       f"The file's content is {ALLOWED_MIME[mime]}, which does not match its "
                       f"{'.' + ext if ext else 'missing'} extension. Rename it or export it properly.", 400, "file")
    count = len(session.exec(select(EvidenceFile.id).where(
        EvidenceFile.response_set_id == rs.id, EvidenceFile.section_code == section_code,
        EvidenceFile.question_id == question_id, EvidenceFile.deleted_at.is_(None))).all())
    if count >= MAX_FILES_PER_UPLOAD_TARGET:
        raise AppError("too_many_files", f"At most {MAX_FILES_PER_UPLOAD_TARGET} files per question", 409)

    name = display_filename(filename)
    ev = EvidenceFile(company_id=rs.company_id, response_set_id=rs.id, section_code=section_code,
                      question_id=question_id, assurance_request_id=assurance_request_id,
                      description=(description or "").strip()[:500] or None, original_filename=name,
                      mime_type=mime, size_bytes=0, sha256_checksum="", storage_key="", uploaded_by=actor.id)
    ev.storage_key = _storage_key(rs, ev.public_id, name)
    stored = get_storage().put(ev.storage_key, io.BytesIO(content), mime_type=mime)
    ev.size_bytes = stored.size_bytes
    ev.sha256_checksum = stored.sha256_checksum
    session.add(ev)
    session.flush()
    log_action(session, actor, "evidence.uploaded", "evidence_file", ev.id, ev.public_id, company_id=rs.company_id,
               changes={"response_set": rs.public_id, "section": section_code, "question": question_id,
                        "filename": name, "size_bytes": ev.size_bytes, "sha256": ev.sha256_checksum,
                        "assurance_request_id": assurance_request_id},
               ip_address=ip_address)
    return ev


def upload_for_section(session: Session, actor: User, rs: BrsrResponseSet, section_status: str, *,
                       section_code: str, question_id: Optional[str], filename: str, content: bytes,
                       description: Optional[str], ip_address: Optional[str] = None) -> EvidenceFile:
    if section_status not in EDITABLE_STATUSES:
        raise AppError("section_locked",
                       f"Section {section_code} is {section_status.replace('_', ' ')}. Evidence can be added while "
                       f"it is being prepared, or in answer to an assurance request.", 409)
    ev = store_evidence(session, actor, rs, section_code=section_code, question_id=question_id,
                        filename=filename, content=content, description=description, ip_address=ip_address)
    session.commit()
    session.refresh(ev)
    return ev


def get_evidence(session: Session, rs: BrsrResponseSet, public_id: str) -> EvidenceFile:
    ev = session.exec(select(EvidenceFile).where(EvidenceFile.public_id == public_id,
                                                 EvidenceFile.response_set_id == rs.id)).first()
    if not ev:
        raise NotFoundError("Evidence file not found")
    return ev


def list_evidence(session: Session, rs: BrsrResponseSet, section_code: Optional[str] = None,
                  include_withdrawn: bool = False) -> list[EvidenceFile]:
    stmt = select(EvidenceFile).where(EvidenceFile.response_set_id == rs.id)
    if section_code:
        stmt = stmt.where(EvidenceFile.section_code == section_code)
    if not include_withdrawn:
        stmt = stmt.where(EvidenceFile.deleted_at.is_(None))
    return list(session.exec(stmt.order_by(EvidenceFile.uploaded_at)).all())


def is_relied_on(session: Session, ev: EvidenceFile) -> Optional[str]:
    """Why assurance depends on this file (so it cannot be withdrawn), or None."""
    from app.models.assurance import AssuranceEngagement, AssuranceRequest
    if ev.assurance_request_id:
        req = session.get(AssuranceRequest, ev.assurance_request_id)
        if req and req.status != "open":
            return "it was submitted in answer to an assurance request"
    signed = session.exec(select(AssuranceEngagement.id).where(
        AssuranceEngagement.response_set_id == ev.response_set_id,
        AssuranceEngagement.status == "signed")).first()
    if signed:
        return "the BRSR has been signed off by the assurance provider"
    return None


def withdraw_evidence(session: Session, actor: User, rs: BrsrResponseSet, ev: EvidenceFile, section_status: str,
                      ip_address: Optional[str] = None) -> EvidenceFile:
    if ev.deleted_at:
        raise AppError("invalid_state", "This file has already been withdrawn", 409)
    if ev.uploaded_by != actor.id:
        raise AppError("forbidden", "Only the person who uploaded a file can withdraw it", 403)
    reason = is_relied_on(session, ev)
    if reason:
        raise AppError("evidence_locked", f"This file can no longer be withdrawn: {reason}.", 409)
    if not ev.assurance_request_id and section_status not in EDITABLE_STATUSES:
        raise AppError("section_locked", f"Section {ev.section_code} is locked; its evidence can't be withdrawn", 409)
    ev.deleted_at = datetime.utcnow()
    ev.deleted_by = actor.id
    session.add(ev)
    log_action(session, actor, "evidence.withdrawn", "evidence_file", ev.id, ev.public_id, company_id=rs.company_id,
               changes={"filename": ev.original_filename, "section": ev.section_code}, ip_address=ip_address)
    session.commit()
    session.refresh(ev)
    return ev


def download_url(session: Session, actor: User, ev: EvidenceFile, ip_address: Optional[str] = None) -> dict:
    if ev.deleted_at:
        raise NotFoundError("Evidence file not found")
    url = get_storage().signed_url(ev.storage_key, expires_in=300)
    log_action(session, actor, "evidence.downloaded", "evidence_file", ev.id, ev.public_id,
               company_id=ev.company_id, ip_address=ip_address)
    session.commit()
    return {"url": url, "expires_in": 300, "filename": ev.original_filename}


def serialize(ev: EvidenceFile, names: dict[int, str]) -> dict:
    return {
        "public_id": ev.public_id, "section_code": ev.section_code, "question_id": ev.question_id,
        "filename": ev.original_filename, "mime_type": ev.mime_type, "kind": ALLOWED_MIME.get(ev.mime_type),
        "size_bytes": ev.size_bytes, "sha256": ev.sha256_checksum, "description": ev.description,
        "uploaded_by": names.get(ev.uploaded_by), "uploaded_by_id": ev.uploaded_by, "uploaded_at": ev.uploaded_at,
        "for_assurance_request": ev.assurance_request_id is not None,
        "withdrawn": ev.deleted_at is not None,
    }


def user_names(session: Session, ids) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u.name for u in session.exec(select(User).where(User.id.in_(ids))).all()}
