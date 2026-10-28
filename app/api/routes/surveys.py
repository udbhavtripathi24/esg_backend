"""Surveys & assessments API. Thin HTTP layer over app/surveys/.

Three audiences:
    /surveys/…          people who build and run surveys      assessment:manage
    /my-surveys/…       signed-in respondents                  any user in the survey's audience
    /public/surveys/…   people answering by link, no account   the link is the credential (rate-limited)

The public endpoints take the draft's resume key in the X-Resume-Key header
(never the URL, so it doesn't end up in logs or browser history).
"""
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_user, require_permission
from app.core import ratelimit
from app.db.session import get_session
from app.models.user import User
from app.services.audit import log_action
from app.surveys import results, service

router = APIRouter(tags=["surveys"])
manage = require_permission("assessment:manage")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------
# Managing
# ---------------------------------------------------------------------------

class CreateBody(BaseModel):
    company_id: Optional[int] = None
    template_id: Optional[str] = None
    title: Optional[str] = Field(None, max_length=200)
    kind: Optional[Literal["survey", "assessment"]] = None
    category: Optional[str] = Field(None, max_length=60)
    description: Optional[str] = Field(None, max_length=2000)


class UpdateBody(BaseModel):
    revision: Optional[int] = None
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    welcome: Optional[str] = Field(None, max_length=4000)
    thank_you: Optional[str] = Field(None, max_length=2000)
    category: Optional[str] = Field(None, max_length=60)
    kind: Optional[Literal["survey", "assessment"]] = None
    anonymous: Optional[bool] = None
    open_to_company: Optional[bool] = None
    show_score_to_respondent: Optional[bool] = None
    opens_at: Optional[datetime] = None
    closes_at: Optional[datetime] = None
    definition: Optional[dict[str, Any]] = None


class EmailInvite(BaseModel):
    email: str = Field(..., max_length=254)
    name: Optional[str] = Field(None, max_length=120)
    group: Optional[str] = Field(None, max_length=120)


class InviteBody(BaseModel):
    user_ids: list[int] = Field(default_factory=list, max_length=5000)
    departments: list[str] = Field(default_factory=list, max_length=200)
    emails: list[EmailInvite] = Field(default_factory=list, max_length=5000)


class LinksBody(BaseModel):
    invite_ids: Optional[list[str]] = None


class PublicLinkBody(BaseModel):
    enabled: bool
    regenerate: bool = False


class TemplateBody(BaseModel):
    title: Optional[str] = Field(None, max_length=200)


@router.get("/surveys")
def list_surveys(company_id: Optional[int] = None, session: Session = Depends(get_session),
                 actor: User = Depends(manage)):
    return service.list_managed(session, actor, company_id)


@router.get("/surveys/templates")
def list_templates(company_id: Optional[int] = None, session: Session = Depends(get_session),
                   actor: User = Depends(manage)):
    return service.template_list(session, actor, company_id)


@router.get("/surveys/templates/{template_id}")
def get_template(template_id: str, company_id: Optional[int] = None, session: Session = Depends(get_session),
                 actor: User = Depends(manage)):
    return service.template_preview(session, actor, template_id, company_id)


@router.post("/surveys", status_code=201)
def create_survey(body: CreateBody, request: Request, session: Session = Depends(get_session),
                  actor: User = Depends(manage)):
    sv = service.create_survey(session, actor, body.company_id, body.model_dump(exclude_unset=True),
                               ip_address=_ip(request))
    return service.detail(session, sv)


@router.get("/surveys/{survey_id}")
def get_survey(survey_id: str, session: Session = Depends(get_session), actor: User = Depends(manage)):
    return service.detail(session, service.get_managed(session, actor, survey_id))


@router.put("/surveys/{survey_id}")
def update_survey(survey_id: str, body: UpdateBody, request: Request, session: Session = Depends(get_session),
                  actor: User = Depends(manage)):
    sv = service.get_managed(session, actor, survey_id)
    sv = service.update_survey(session, actor, sv, body.model_dump(exclude_unset=True), ip_address=_ip(request))
    return service.detail(session, sv)


@router.delete("/surveys/{survey_id}", status_code=204)
def delete_survey(survey_id: str, request: Request, session: Session = Depends(get_session),
                  actor: User = Depends(manage)):
    service.delete_survey(session, actor, service.get_managed(session, actor, survey_id), ip_address=_ip(request))
    return Response(status_code=204)


@router.post("/surveys/{survey_id}/publish")
def publish(survey_id: str, request: Request, session: Session = Depends(get_session), actor: User = Depends(manage)):
    sv = service.publish(session, actor, service.get_managed(session, actor, survey_id), ip_address=_ip(request))
    return service.detail(session, sv)


@router.post("/surveys/{survey_id}/duplicate", status_code=201)
def duplicate(survey_id: str, request: Request, session: Session = Depends(get_session),
              actor: User = Depends(manage)):
    sv = service.duplicate(session, actor, service.get_managed(session, actor, survey_id), ip_address=_ip(request))
    return service.detail(session, sv)


@router.post("/surveys/{survey_id}/save-as-template", status_code=201)
def save_as_template(survey_id: str, body: TemplateBody, request: Request, session: Session = Depends(get_session),
                     actor: User = Depends(manage)):
    sv = service.duplicate(session, actor, service.get_managed(session, actor, survey_id), as_template=True,
                           title=body.title, ip_address=_ip(request))
    return service.detail(session, sv)


@router.put("/surveys/{survey_id}/public-link")
def public_link(survey_id: str, body: PublicLinkBody, request: Request, session: Session = Depends(get_session),
                actor: User = Depends(manage)):
    sv = service.set_public_link(session, actor, service.get_managed(session, actor, survey_id), body.enabled,
                                 body.regenerate, ip_address=_ip(request))
    return service.detail(session, sv)


@router.get("/surveys/{survey_id}/audience")
def audience(survey_id: str, session: Session = Depends(get_session), actor: User = Depends(manage)):
    sv = service.get_managed(session, actor, survey_id)
    return {**service.audience_options(session, sv), "invites": service.invite_rows(session, sv),
            "counts": service.audience_counts(session, sv)}


@router.post("/surveys/{survey_id}/invites", status_code=201)
def invite(survey_id: str, body: InviteBody, request: Request, session: Session = Depends(get_session),
           actor: User = Depends(manage)):
    sv = service.get_managed(session, actor, survey_id)
    return service.add_invites(session, actor, sv, user_ids=body.user_ids, departments=body.departments,
                               emails=[e.model_dump() for e in body.emails], ip_address=_ip(request))


@router.delete("/surveys/{survey_id}/invites/{invite_id}", status_code=204)
def remove_invite(survey_id: str, invite_id: str, request: Request, session: Session = Depends(get_session),
                  actor: User = Depends(manage)):
    sv = service.get_managed(session, actor, survey_id)
    service.remove_invite(session, actor, sv, service.get_invite(session, sv, invite_id), ip_address=_ip(request))
    return Response(status_code=204)


@router.post("/surveys/{survey_id}/links")
def renew_links(survey_id: str, body: LinksBody, request: Request, session: Session = Depends(get_session),
                actor: User = Depends(manage)):
    sv = service.get_managed(session, actor, survey_id)
    return {"links": service.new_links(session, actor, sv, body.invite_ids, ip_address=_ip(request))}


@router.post("/surveys/{survey_id}/remind")
def remind(survey_id: str, request: Request, session: Session = Depends(get_session), actor: User = Depends(manage)):
    return service.remind(session, actor, service.get_managed(session, actor, survey_id), ip_address=_ip(request))


@router.get("/surveys/{survey_id}/results")
def survey_results(survey_id: str, group: Optional[str] = None, session: Session = Depends(get_session),
                   actor: User = Depends(manage)):
    return results.build(session, service.get_managed(session, actor, survey_id), group)


@router.get("/surveys/{survey_id}/responses")
def response_list(survey_id: str, session: Session = Depends(get_session), actor: User = Depends(manage)):
    return results.response_list(session, service.get_managed(session, actor, survey_id))


@router.get("/surveys/{survey_id}/responses/{response_id}")
def response_detail(survey_id: str, response_id: str, session: Session = Depends(get_session),
                    actor: User = Depends(manage)):
    return results.response_detail(session, service.get_managed(session, actor, survey_id), response_id)


@router.get("/surveys/{survey_id}/export")
def export(survey_id: str, request: Request, session: Session = Depends(get_session), actor: User = Depends(manage)):
    sv = service.get_managed(session, actor, survey_id)
    data = results.export_xlsx(session, sv)
    log_action(session, actor, "survey.exported", "survey", sv.id, sv.public_id, company_id=sv.company_id,
                       ip_address=_ip(request))
    session.commit()
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in sv.title)[:60].strip() or "survey"
    return Response(data, media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{safe} - results.xlsx"'})


@router.get("/surveys/{survey_id}/files/{file_id}/download")
def download_file(survey_id: str, file_id: str, request: Request, session: Session = Depends(get_session),
                  actor: User = Depends(manage)):
    sv = service.get_managed(session, actor, survey_id)
    return service.file_download(session, actor, sv, file_id, ip_address=_ip(request))


# Declared after every other POST /surveys/{id}/<word> route, which it would otherwise shadow
@router.post("/surveys/{survey_id}/{action}")
def change_status(survey_id: str, action: Literal["close", "reopen", "archive", "restore"], request: Request,
                  session: Session = Depends(get_session), actor: User = Depends(manage)):
    sv = service.set_status(session, actor, service.get_managed(session, actor, survey_id), action,
                            ip_address=_ip(request))
    return service.detail(session, sv)


# ---------------------------------------------------------------------------
# Signed-in respondents
# ---------------------------------------------------------------------------

class AnswersBody(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    revision: Optional[int] = None


class SubmitBody(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    name: Optional[str] = Field(None, max_length=120)
    organisation: Optional[str] = Field(None, max_length=120)


@router.get("/my-surveys")
def my_surveys(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return service.my_surveys(session, user)


def _mine(session: Session, user: User, survey_id: str, create: bool = False):
    sv = service._by_public_id(session, survey_id)
    return sv, service.user_respondent(session, sv, user, create=create)


@router.get("/my-surveys/{survey_id}")
def my_survey(survey_id: str, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    sv, who = _mine(session, user, survey_id)
    return service.form_payload(session, sv, who)


@router.put("/my-surveys/{survey_id}/response")
def my_save(survey_id: str, body: AnswersBody, session: Session = Depends(get_session),
            user: User = Depends(get_current_user)):
    sv, who = _mine(session, user, survey_id, create=True)
    return service.save_draft(session, sv, who, body.answers, body.revision)


@router.post("/my-surveys/{survey_id}/submit")
def my_submit(survey_id: str, body: SubmitBody, request: Request, session: Session = Depends(get_session),
              user: User = Depends(get_current_user)):
    sv, who = _mine(session, user, survey_id, create=True)
    return service.submit(session, sv, who, body.answers, ip_address=_ip(request))


@router.post("/my-surveys/{survey_id}/files", status_code=201)
async def my_upload(survey_id: str, file: UploadFile = File(...), question_id: str = Form(...),
                    session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    sv, who = _mine(session, user, survey_id, create=True)
    content = await file.read(service.MAX_FILE_BYTES + 1)
    return service.upload_file(session, sv, who, question_id, file.filename or "file", content)


@router.delete("/my-surveys/{survey_id}/files/{file_id}")
def my_remove_file(survey_id: str, file_id: str, session: Session = Depends(get_session),
                   user: User = Depends(get_current_user)):
    sv, who = _mine(session, user, survey_id)
    return service.remove_file(session, sv, who, file_id)


# ---------------------------------------------------------------------------
# By link (no account)
# ---------------------------------------------------------------------------

def _limit(request: Request, bucket: str, n: int) -> None:
    ratelimit.check(f"survey_public_{bucket}", _ip(request), n)


def _linked(session: Session, token: str, resume_key: Optional[str]):
    sv, who = service.by_link(session, token)
    if who.channel == "public":
        who.resume_key = (resume_key or "").strip()[:100] or None
    return sv, who


@router.get("/public/surveys/{token}")
def public_form(token: str, request: Request, x_resume_key: Optional[str] = Header(None),
                session: Session = Depends(get_session)):
    _limit(request, "read", 120)
    sv, who = _linked(session, token, x_resume_key)
    return service.form_payload(session, sv, who)


@router.put("/public/surveys/{token}/response")
def public_save(token: str, body: AnswersBody, request: Request, x_resume_key: Optional[str] = Header(None),
                session: Session = Depends(get_session)):
    _limit(request, "save", 60)
    sv, who = _linked(session, token, x_resume_key)
    return service.save_draft(session, sv, who, body.answers, body.revision)


@router.post("/public/surveys/{token}/submit")
def public_submit(token: str, body: SubmitBody, request: Request, x_resume_key: Optional[str] = Header(None),
                  session: Session = Depends(get_session)):
    _limit(request, "submit", 10)
    sv, who = _linked(session, token, x_resume_key)
    return service.submit(session, sv, who, body.answers, respondent_name=body.name, organisation=body.organisation,
                          ip_address=_ip(request))


@router.post("/public/surveys/{token}/files", status_code=201)
async def public_upload(token: str, request: Request, file: UploadFile = File(...), question_id: str = Form(...),
                        x_resume_key: Optional[str] = Header(None), session: Session = Depends(get_session)):
    _limit(request, "upload", 20)
    sv, who = _linked(session, token, x_resume_key)
    content = await file.read(service.MAX_FILE_BYTES + 1)
    return service.upload_file(session, sv, who, question_id, file.filename or "file", content)


@router.delete("/public/surveys/{token}/files/{file_id}")
def public_remove_file(token: str, file_id: str, request: Request, x_resume_key: Optional[str] = Header(None),
                       session: Session = Depends(get_session)):
    _limit(request, "save", 60)
    sv, who = _linked(session, token, x_resume_key)
    return service.remove_file(session, sv, who, file_id)
