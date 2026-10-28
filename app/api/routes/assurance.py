"""Assurance API. Thin HTTP layer over app/assurance/service.py.

    view engagements, download documents     assurance:read
    answer requests / findings, upload       assurance:respond (client, own company)
    create engagement, team, start, withdraw assurance:manage
    workpapers, requests, findings, EQR      assurance:perform (team members only)
    sign the opinion                         assurance:sign (the engagement partner)
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import require_permission
from app.api.routes.datasets import content_disposition
from app.assurance import documents
from app.assurance import service as svc
from app.brsr import evidence as ev_service
from app.core.errors import AppError, NotFoundError
from app.db.session import get_session
from app.models.user import User

router = APIRouter(prefix="/assurance", tags=["assurance"])


def _ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _detail(session, actor, eng, access):
    return svc.detail(session, actor, eng, access)


# ----------------------------------------------------------------- engagements

@router.get("/engagements")
def list_engagements(session: Session = Depends(get_session),
                     actor: User = Depends(require_permission("assurance:read"))):
    return svc.list_engagements(session, actor)


@router.get("/assurable-sets")
def assurable_sets(session: Session = Depends(get_session),
                   actor: User = Depends(require_permission("assurance:manage"))):
    return svc.assurable_sets(session, actor)


class CreateBody(BaseModel):
    response_set_id: str
    provider_name: str
    level: str = "limited"
    scope: Optional[list[str]] = None
    standard: Optional[str] = None


@router.post("/engagements", status_code=201)
def create_engagement(body: CreateBody, request: Request, session: Session = Depends(get_session),
                      actor: User = Depends(require_permission("assurance:manage"))):
    eng = svc.create_engagement(session, actor, response_set_id=body.response_set_id,
                                provider_name=body.provider_name, level=body.level, scope=body.scope,
                                standard=body.standard, ip_address=_ip(request))
    return _detail(session, actor, eng, "manager")


@router.get("/engagements/{eng_id}")
def get_engagement(eng_id: str, session: Session = Depends(get_session),
                   actor: User = Depends(require_permission("assurance:read"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    return _detail(session, actor, eng, access)


class ReasonBody(BaseModel):
    reason: str = ""


@router.post("/engagements/{eng_id}/withdraw")
def withdraw(eng_id: str, body: ReasonBody, request: Request, session: Session = Depends(get_session),
             actor: User = Depends(require_permission("assurance:manage"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.withdraw_engagement(session, actor, eng, body.reason, ip_address=_ip(request))
    return _detail(session, actor, eng, access)


# ------------------------------------------------------------------------ team

@router.get("/engagements/{eng_id}/candidates")
def candidates(eng_id: str, session: Session = Depends(get_session),
               actor: User = Depends(require_permission("assurance:manage"))):
    eng, _ = svc.get_engagement(session, actor, eng_id)
    return svc.candidates(session, actor, eng)


class MemberBody(BaseModel):
    user_id: int
    role: str


@router.post("/engagements/{eng_id}/members", status_code=201)
def add_member(eng_id: str, body: MemberBody, request: Request, session: Session = Depends(get_session),
               actor: User = Depends(require_permission("assurance:manage"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.add_member(session, actor, eng, user_id=body.user_id, role=body.role, ip_address=_ip(request))
    return _detail(session, actor, eng, access)


@router.delete("/engagements/{eng_id}/members/{user_id}")
def remove_member(eng_id: str, user_id: int, request: Request, session: Session = Depends(get_session),
                  actor: User = Depends(require_permission("assurance:manage"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.remove_member(session, actor, eng, user_id, ip_address=_ip(request))
    return _detail(session, actor, eng, access)


@router.post("/engagements/{eng_id}/independence")
def declare_independence(eng_id: str, request: Request, session: Session = Depends(get_session),
                         actor: User = Depends(require_permission("assurance:perform"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.declare_independence(session, actor, eng, ip_address=_ip(request))
    return _detail(session, actor, eng, access)


@router.post("/engagements/{eng_id}/start")
def start(eng_id: str, request: Request, session: Session = Depends(get_session),
          actor: User = Depends(require_permission("assurance:manage"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.start_fieldwork(session, actor, eng, ip_address=_ip(request))
    return _detail(session, actor, eng, access)


# ------------------------------------------------------------------ workpapers

@router.get("/engagements/{eng_id}/sections/{code}")
def section(eng_id: str, code: str, session: Session = Depends(get_session),
            actor: User = Depends(require_permission("assurance:read"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    return svc.section_for_assurer(session, actor, eng, access, code)


class PrepareBody(BaseModel):
    procedures: str = ""
    conclusion: str = ""


@router.post("/engagements/{eng_id}/items/{code}/prepare")
def prepare(eng_id: str, code: str, body: PrepareBody, request: Request, session: Session = Depends(get_session),
            actor: User = Depends(require_permission("assurance:perform"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.prepare_item(session, actor, eng, code, procedures=body.procedures, conclusion=body.conclusion,
                     ip_address=_ip(request))
    return _detail(session, actor, eng, access)


class DecisionBody(BaseModel):
    decision: str
    note: Optional[str] = None


@router.post("/engagements/{eng_id}/items/{code}/review")
def review(eng_id: str, code: str, body: DecisionBody, request: Request, session: Session = Depends(get_session),
           actor: User = Depends(require_permission("assurance:perform"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.review_item(session, actor, eng, code, decision=body.decision, note=body.note, ip_address=_ip(request))
    return _detail(session, actor, eng, access)


# -------------------------------------------------------------------- requests

class RequestBody(BaseModel):
    section_code: str
    question_id: Optional[str] = None
    title: str = ""
    detail: Optional[str] = None
    due_date: Optional[date] = None


@router.post("/engagements/{eng_id}/requests", status_code=201)
def raise_request(eng_id: str, body: RequestBody, request: Request, session: Session = Depends(get_session),
                  actor: User = Depends(require_permission("assurance:perform"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.raise_request(session, actor, eng, section_code=body.section_code, question_id=body.question_id or None,
                      title=body.title, detail=body.detail, due_date=body.due_date, ip_address=_ip(request))
    return _detail(session, actor, eng, access)


class RespondBody(BaseModel):
    response: str = ""


@router.post("/engagements/{eng_id}/requests/{req_id}/evidence", status_code=201)
async def request_evidence(eng_id: str, req_id: str, request: Request, file: UploadFile = File(...),
                           description: Optional[str] = Form(None), session: Session = Depends(get_session),
                           actor: User = Depends(require_permission("assurance:respond"))):
    eng, _ = svc.get_engagement(session, actor, eng_id)
    r = svc.get_request(session, eng, req_id)
    content = await file.read(ev_service.MAX_EVIDENCE_BYTES + 1)
    ev = svc.upload_request_evidence(session, actor, eng, r, filename=file.filename or "evidence", content=content,
                                     description=description, ip_address=_ip(request))
    return ev_service.serialize(ev, {actor.id: actor.name})


@router.post("/engagements/{eng_id}/requests/{req_id}/respond")
def respond_request(eng_id: str, req_id: str, body: RespondBody, request: Request,
                    session: Session = Depends(get_session),
                    actor: User = Depends(require_permission("assurance:respond"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.respond_request(session, actor, eng, svc.get_request(session, eng, req_id), response=body.response,
                        ip_address=_ip(request))
    return _detail(session, actor, eng, access)


@router.post("/engagements/{eng_id}/requests/{req_id}/close")
def close_request(eng_id: str, req_id: str, body: DecisionBody, request: Request,
                  session: Session = Depends(get_session),
                  actor: User = Depends(require_permission("assurance:perform"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.close_request(session, actor, eng, svc.get_request(session, eng, req_id), decision=body.decision,
                      note=body.note, ip_address=_ip(request))
    return _detail(session, actor, eng, access)


# -------------------------------------------------------------------- findings

class FindingBody(BaseModel):
    section_code: str
    question_id: Optional[str] = None
    severity: str
    description: str = ""


@router.post("/engagements/{eng_id}/findings", status_code=201)
def raise_finding(eng_id: str, body: FindingBody, request: Request, session: Session = Depends(get_session),
                  actor: User = Depends(require_permission("assurance:perform"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.raise_finding(session, actor, eng, section_code=body.section_code, question_id=body.question_id or None,
                      severity=body.severity, description=body.description, ip_address=_ip(request))
    return _detail(session, actor, eng, access)


@router.post("/engagements/{eng_id}/findings/{finding_id}/respond")
def respond_finding(eng_id: str, finding_id: str, body: RespondBody, request: Request,
                    session: Session = Depends(get_session),
                    actor: User = Depends(require_permission("assurance:respond"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.respond_finding(session, actor, eng, svc.get_finding(session, eng, finding_id), response=body.response,
                        ip_address=_ip(request))
    return _detail(session, actor, eng, access)


class ResolveBody(BaseModel):
    outcome: str
    note: Optional[str] = None


@router.post("/engagements/{eng_id}/findings/{finding_id}/resolve")
def resolve_finding(eng_id: str, finding_id: str, body: ResolveBody, request: Request,
                    session: Session = Depends(get_session),
                    actor: User = Depends(require_permission("assurance:perform"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.resolve_finding(session, actor, eng, svc.get_finding(session, eng, finding_id), outcome=body.outcome,
                        note=body.note, ip_address=_ip(request))
    return _detail(session, actor, eng, access)


# ------------------------------------------------------------- review and sign

class NoteBody(BaseModel):
    note: Optional[str] = None


@router.post("/engagements/{eng_id}/eqr-clear")
def eqr_clear(eng_id: str, body: NoteBody, request: Request, session: Session = Depends(get_session),
              actor: User = Depends(require_permission("assurance:perform"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.clear_eqr(session, actor, eng, body.note, ip_address=_ip(request))
    return _detail(session, actor, eng, access)


class SignBody(BaseModel):
    opinion: str
    basis: Optional[str] = None
    statement: Optional[str] = Field(default=None, description="Leave empty to use the standard wording")


@router.post("/engagements/{eng_id}/sign")
def sign(eng_id: str, body: SignBody, request: Request, session: Session = Depends(get_session),
         actor: User = Depends(require_permission("assurance:sign"))):
    eng, access = svc.get_engagement(session, actor, eng_id)
    svc.sign(session, actor, eng, opinion=body.opinion, basis=body.basis, statement=body.statement,
             ip_address=_ip(request))
    return _detail(session, actor, eng, access)


# ------------------------------------------------------------------- downloads

@router.get("/engagements/{eng_id}/evidence/{evidence_id}/download")
def download_evidence(eng_id: str, evidence_id: str, request: Request, session: Session = Depends(get_session),
                      actor: User = Depends(require_permission("assurance:read"))):
    eng, _ = svc.get_engagement(session, actor, eng_id)
    ev = ev_service.get_evidence(session, svc._set_of(session, eng), evidence_id)
    return ev_service.download_url(session, actor, ev, ip_address=_ip(request))


@router.get("/engagements/{eng_id}/documents/{kind}")
def signed_document(eng_id: str, kind: str, session: Session = Depends(get_session),
                    actor: User = Depends(require_permission("assurance:read"))):
    eng, _ = svc.get_engagement(session, actor, eng_id)
    if kind not in ("statement", "register"):
        raise NotFoundError("Unknown document")
    if eng.status != "signed":
        raise AppError("not_signed", "The statement and register are issued when the partner signs", 409)
    return documents.signed_document_url(eng, kind)


@router.get("/engagements/{eng_id}/register-draft")
def register_draft(eng_id: str, session: Session = Depends(get_session),
                   actor: User = Depends(require_permission("assurance:read"))):
    """Live sign-off register while the engagement is in progress."""
    eng, access = svc.get_engagement(session, actor, eng_id)
    data = documents.build_register_xlsx(session, eng)
    name = f"BRSR_FY{eng.financial_year}_signoff_register_DRAFT.xlsx"
    return StreamingResponse(iter([data]), media_type=documents.XLSX_MIME,
                             headers={"Content-Disposition": content_disposition(name)})


@router.get("/engagements/{eng_id}/pack")
def pack(eng_id: str, request: Request, session: Session = Depends(get_session),
         actor: User = Depends(require_permission("assurance:read"))):
    eng, _ = svc.get_engagement(session, actor, eng_id)
    if eng.status != "signed":
        raise AppError("not_signed", "The assurance pack is available once the partner signs", 409)
    from app.services.audit import log_action
    f = documents.build_pack(session, eng)
    log_action(session, actor, "assurance.pack.downloaded", "assurance_engagement", eng.id, eng.public_id,
               company_id=eng.company_id, ip_address=_ip(request))
    session.commit()

    def chunks():
        try:
            while True:
                b = f.read(1024 * 1024)
                if not b:
                    break
                yield b
        finally:
            f.close()

    return StreamingResponse(chunks(), media_type="application/zip", headers={
        "Content-Disposition": content_disposition(f"BRSR_FY{eng.financial_year}_assurance_pack.zip")})
