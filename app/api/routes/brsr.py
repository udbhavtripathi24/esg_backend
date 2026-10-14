"""BRSR questionnaire API. Thin HTTP layer over app/brsr/service.py.

Permissions reuse the existing data-collection permissions, so no RBAC
migration is needed and every existing role behaves as it already does
for dataset uploads:
    read answers / question bank   dataset:read
    create a response set, save    dataset:create
    submit / withdraw / reopen     dataset:submit
    approve / request changes      dataset:review
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import require_permission
from app.brsr import service
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
    return service.section_detail(session, rs, code)


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
    return service.section_detail(session, rs, code)


@router.post("/response-sets/{set_id}/sections/{code}/withdraw")
def withdraw_section(set_id: str, code: str, request: Request, session: Session = Depends(get_session),
                     actor: User = Depends(require_permission("dataset:submit"))):
    rs = service.get_set(session, actor, set_id)
    service.withdraw_section(session, actor, rs, code, ip_address=_ip(request))
    return service.section_detail(session, rs, code)


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
    return service.section_detail(session, rs, code)


class ReopenBody(BaseModel):
    reason: str


@router.post("/response-sets/{set_id}/sections/{code}/reopen")
def reopen_section(set_id: str, code: str, body: ReopenBody, request: Request,
                   session: Session = Depends(get_session),
                   actor: User = Depends(require_permission("dataset:submit"))):
    rs = service.get_set(session, actor, set_id)
    service.reopen_section(session, actor, rs, code, body.reason, ip_address=_ip(request))
    return service.section_detail(session, rs, code)


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
