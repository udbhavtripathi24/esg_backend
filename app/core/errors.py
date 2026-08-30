"""Uniform error contract: {"error": {"code","message","field"}} (API §2).

Cross-tenant access returns 404 (not 403) so record existence never leaks
across tenants (decision #5).
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, field: str | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.field = field


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__("not_found", message, status_code=404)


class PermissionError_(AppError):
    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__("forbidden", message, status_code=403)


def _body(code: str, message: str, field: str | None = None):
    return {"error": {"code": code, "message": message, "field": field}}


def register_error_handlers(app):
    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message, exc.field))

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", [])[1:]) or None
        return JSONResponse(status_code=422, content=_body("validation_error", first.get("msg", "Invalid request"), field))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 401: "unauthorized", 403: "forbidden"}.get(exc.status_code, "error")
        return JSONResponse(status_code=exc.status_code, content=_body(code, str(exc.detail)))
