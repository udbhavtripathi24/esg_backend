"""BRSR questionnaire API. Thin HTTP layer over app/brsr/service.py.

Permissions reuse the existing data-collection permissions, so no RBAC
migration is needed and every existing role behaves as it already does
for dataset uploads:
    read answers / question bank   dataset:read
    create a response set, save    dataset:create
    submit / withdraw / reopen     dataset:submit
    approve / request changes      dataset:review
    attach / withdraw evidence     file:upload
    download evidence              file:download
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import require_permission
from app.brsr import evidence as ev_service
from app.brsr import service, workflow
from app.brsr.question_bank import public_bank
from app.db.session import get_session
from app.models.user import User

router = APIRouter(prefix="/brsr", tags=["brsr"])


def _ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


@router.get("/question-bank")
def question_bank(_actor: User = Depends(require_permission("dataset:read"))):
    return public_bank()


@router.get("/response-sets")
def list_response_sets(company_id: Optional[int] = None, session: Session = Depends(get_session),
                       actor: User = Depends(require_permission("dataset:read"))):
    return service.list_sets(session, actor, company_id)


class CreateBody(BaseModel):
    financial_year: str
    company_id: Optional[int] = None


@router.post("/response-sets", status_code=201)
def create_response_set(body: CreateBody, request: Request, session: Session = Depends(get_session),
                        actor: User = Depends(require_permission("dataset:create"))):
    company_id = service.resolve_company_id(session, actor, body.company_id)
    rs = service.create_set(session, actor, company_id, body.financial_year, ip_address=_ip(request))
    return service.overview(session, rs)


@router.get("/response-sets/{set_id}")
def get_response_set(set_id: str, session: Session = Depends(get_session),
                     actor: User = Depends(require_permission("dataset:read"))):
    return service.overview(session, service.get_set(session, actor, set_id))


@router.get("/response-sets/{set_id}/sections/{code}")
def get_section(set_id: str, code: str, session: Session = Depends(get_session),
                actor: User = Depends(require_permission("dataset:read"))):
    rs = service.get_set(session, actor, set_id)
    return service.section_detail(session, rs, code, actor)


class SaveItem(BaseModel):
    value: dict
    revision: Optional[int] = None


class SaveBody(BaseModel):
    answers: dict[str, SaveItem] = Field(default_factory=dict)


@router.put("/response-sets/{set_id}/answers")
def save_answers(set_id: str, body: SaveBody, request: Request, session: Session = Depends(get_session),
                 actor: User = Depends(require_permission("dataset:create"))):
    rs = service.get_set(session, actor, set_id)
    try:
        return service.save_answers(session, actor, rs, {k: v.model_dump() for k, v in body.answers.items()},
                                    ip_address=_ip(request))
    except service.ConflictError as exc:
        session.rollback()
        return JSONResponse(status_code=409, content={
            "error": {"code": exc.code, "message": exc.message, "field": None}, "conflicts": exc.conflicts})


@router.get("/response-sets/{set_id}/answers/{question_id}/history")
def answer_history(set_id: str, question_id: str, session: Session = Depends(get_session),
                   actor: User = Depends(require_permission("dataset:read"))):
    rs = service.get_set(session, actor, set_id)
    return service.answer_history(session, rs, question_id)


@router.post("/response-sets/{set_id}/sections/{code}/submit")
def submit_section(set_id: str, code: str, request: Request, session: Session = Depends(get_session),
                   actor: User = Depends(require_permission("dataset:submit"))):
    rs = service.get_set(session, actor, set_id)
    service.submit_section(session, actor, rs, code, ip_address=_ip(request))
    return service.section_detail(session, rs, code, actor)


@router.post("/response-sets/{set_id}/sections/{code}/withdraw")
def withdraw_section(set_id: str, code: str, request: Request, session: Session = Depends(get_session),
                     actor: User = Depends(require_permission("dataset:submit"))):
    rs = service.get_set(session, actor, set_id)
    service.withdraw_section(session, actor, rs, code, ip_address=_ip(request))
    return service.section_detail(session, rs, code, actor)


class QuestionComment(BaseModel):
    question_id: str
    body: str


class DecideBody(BaseModel):
    decision: str
    note: str
    question_comments: list[QuestionComment] = Field(default_factory=list)


@router.post("/response-sets/{set_id}/sections/{code}/decide")
def decide_section(set_id: str, code: str, body: DecideBody, request: Request,
                   session: Session = Depends(get_session),
                   actor: User = Depends(require_permission("dataset:review"))):
    rs = service.get_set(session, actor, set_id)
    service.decide_section(session, actor, rs, code, body.decision, body.note,
                           [c.model_dump() for c in body.question_comments], ip_address=_ip(request))
    return service.section_detail(session, rs, code, actor)


class ReopenBody(BaseModel):
    reason: str


@router.post("/response-sets/{set_id}/sections/{code}/reopen")
def reopen_section(set_id: str, code: str, body: ReopenBody, request: Request,
                   session: Session = Depends(get_session),
                   actor: User = Depends(require_permission("dataset:submit"))):
    rs = service.get_set(session, actor, set_id)
    service.reopen_section(session, actor, rs, code, body.reason, ip_address=_ip(request))
    return service.section_detail(session, rs, code, actor)


# ---------- Per-question workflow (SPOC answers, department reviewer approves)

class QuestionIds(BaseModel):
    question_ids: list[str] = Field(default_factory=list, max_length=200)


class QuestionReviewBody(QuestionIds):
    decision: str
    note: Optional[str] = None


@router.post("/response-sets/{set_id}/questions/submit")
def submit_questions(set_id: str, body: QuestionIds, request: Request, session: Session = Depends(get_session),
                     actor: User = Depends(require_permission("dataset:create"))):
    rs = service.get_set(session, actor, set_id)
    return workflow.submit_questions(session, actor, rs, body.question_ids, ip_address=_ip(request))


@router.post("/response-sets/{set_id}/questions/review")
def review_questions(set_id: str, body: QuestionReviewBody, request: Request,
                     session: Session = Depends(get_session),
                     actor: User = Depends(require_permission("dataset:read"))):
    # Authority comes from the department roster (or review rights where the
    # department has no named reviewer) — checked per question in workflow.
    rs = service.get_set(session, actor, set_id)
    return workflow.review_questions(session, actor, rs, body.question_ids, body.decision, body.note,
                                     ip_address=_ip(request))


@router.get("/question-queue")
def question_queue(session: Session = Depends(get_session),
                   actor: User = Depends(require_permission("dataset:read"))):
    return workflow.question_queue(session, actor)


@router.get("/roster")
def get_roster(company_id: Optional[int] = None, session: Session = Depends(get_session),
               actor: User = Depends(require_permission("dataset:read"))):
    cid = service.resolve_company_id(session, actor, company_id)
    return workflow.roster_view(session, cid)


class RosterEntry(BaseModel):
    department: str
    user_id: int
    role: str


class RosterBody(BaseModel):
    company_id: Optional[int] = None
    entries: list[RosterEntry] = Field(default_factory=list, max_length=1000)


@router.put("/roster")
def put_roster(body: RosterBody, request: Request, session: Session = Depends(get_session),
               actor: User = Depends(require_permission("user:manage"))):
    cid = service.resolve_company_id(session, actor, body.company_id)
    return workflow.set_roster(session, actor, cid, [e.model_dump() for e in body.entries], ip_address=_ip(request))


@router.get("/review-queue")
def review_queue(session: Session = Depends(get_session),
                 actor: User = Depends(require_permission("dataset:review"))):
    return service.review_queue(session, actor)


@router.get("/response-sets/{set_id}/preview")
def preview_docx(set_id: str, fmt: str = Query("docx", pattern="^(docx|pdf)$"),
                 session: Session = Depends(get_session),
                 actor: User = Depends(require_permission("dataset:read"))):
    """Draft preview of the report from the current answers, approved or
    not. Watermarked DRAFT on every page and never stored — the official
    report only comes from Reporting, from a fully approved snapshot."""
    from app.brsr.docx_report import build_brsr_docx, docx_to_pdf
    rs = service.get_set(session, actor, set_id)
    snap = {
        "financial_year": rs.financial_year,
        "company_name": service.overview(session, rs)["company_name"],
        "answers": service.answers_dict(session, rs),
    }
    data = build_brsr_docx(snap, draft=True)
    filename = f"BRSR_FY{rs.financial_year}_DRAFT"
    if fmt == "pdf":
        data = docx_to_pdf(data)
        return Response(content=data, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'})
    return Response(content=data,
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.docx"'})


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@router.get("/response-sets/{set_id}/evidence")
def list_evidence(set_id: str, section: Optional[str] = None, session: Session = Depends(get_session),
                  actor: User = Depends(require_permission("dataset:read"))):
    rs = service.get_set(session, actor, set_id)
    files = ev_service.list_evidence(session, rs, section)
    names = ev_service.user_names(session, [f.uploaded_by for f in files])
    return [ev_service.serialize(f, names) for f in files]


@router.post("/response-sets/{set_id}/sections/{code}/evidence", status_code=201)
async def upload_evidence(set_id: str, code: str, request: Request, file: UploadFile = File(...),
                          question_id: Optional[str] = Form(None), description: Optional[str] = Form(None),
                          session: Session = Depends(get_session),
                          actor: User = Depends(require_permission("file:upload"))):
    rs = service.get_set(session, actor, set_id)
    sec = service.get_section(session, rs, code)
    content = await file.read(ev_service.MAX_EVIDENCE_BYTES + 1)
    ev = ev_service.upload_for_section(session, actor, rs, sec.status, section_code=code,
                                       question_id=question_id or None, filename=file.filename or "evidence",
                                       content=content, description=description, ip_address=_ip(request))
    return ev_service.serialize(ev, {actor.id: actor.name})


@router.get("/response-sets/{set_id}/evidence/{evidence_id}/download")
def download_evidence(set_id: str, evidence_id: str, request: Request, session: Session = Depends(get_session),
                      actor: User = Depends(require_permission("file:download"))):
    rs = service.get_set(session, actor, set_id)
    ev = ev_service.get_evidence(session, rs, evidence_id)
    return ev_service.download_url(session, actor, ev, ip_address=_ip(request))


@router.delete("/response-sets/{set_id}/evidence/{evidence_id}")
def withdraw_evidence(set_id: str, evidence_id: str, request: Request, session: Session = Depends(get_session),
                      actor: User = Depends(require_permission("file:upload"))):
    rs = service.get_set(session, actor, set_id)
    ev = ev_service.get_evidence(session, rs, evidence_id)
    sec = service.get_section(session, rs, ev.section_code)
    ev = ev_service.withdraw_evidence(session, actor, rs, ev, sec.status, ip_address=_ip(request))
    return ev_service.serialize(ev, {actor.id: actor.name})
