from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.errors import register_error_handlers
from app.middleware.request_context import RequestContextMiddleware
from app.db.session import init_db
from app.api.routes import (health, auth, companies, rbac, users, consultant_assignments, master_data, datasets, misc_stage4, reviews)

configure_logging(json_output=settings.is_production)
log = get_logger("startup")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Stage 1: create tables directly for convenience. Alembic migrations are
    # wired up (see alembic/) and are the authoritative path for schema changes.
    init_db()
    log.info("startup_complete", environment=settings.ENVIRONMENT)
    yield


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

# Health probes at root (Cloud Run convention); domain routes under /api/v1.
app.include_router(health.router)
app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
app.include_router(companies.router, prefix=settings.API_V1_PREFIX)
app.include_router(rbac.router, prefix=settings.API_V1_PREFIX)
app.include_router(users.router, prefix=settings.API_V1_PREFIX)
app.include_router(consultant_assignments.router, prefix=settings.API_V1_PREFIX)
# Stage 4
app.include_router(master_data.router, prefix=settings.API_V1_PREFIX)
app.include_router(datasets.router, prefix=settings.API_V1_PREFIX)
app.include_router(misc_stage4.upload_types_router, prefix=settings.API_V1_PREFIX)
app.include_router(misc_stage4.jobs_router, prefix=settings.API_V1_PREFIX)
app.include_router(misc_stage4.audit_router, prefix=settings.API_V1_PREFIX)
app.include_router(misc_stage4.integrations_router, prefix=settings.API_V1_PREFIX)
# Stage 5
app.include_router(reviews.reviews_router, prefix=settings.API_V1_PREFIX)
app.include_router(reviews.comments_router, prefix=settings.API_V1_PREFIX)
app.include_router(reviews.notifications_router, prefix=settings.API_V1_PREFIX)


@app.get("/")
def root():
    return {"status": "ok", "service": "esg-platform-api", "environment": settings.ENVIRONMENT}
