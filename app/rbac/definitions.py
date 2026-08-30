"""Canonical RBAC seed definitions (Stage 2).

Single source of truth for roles, permissions, and role->permission mappings.
The seeder (app/rbac/seed.py) is idempotent and reads only from here, so seeding
is reproducible and re-runnable.

Permission codes follow "<resource>:<action>". Roles mirror the frontend's exact
role strings (decision: reuse frontend enums to avoid a translation layer).
"""

# --- Permissions (resource:action) -----------------------------------------
PERMISSIONS: list[tuple[str, str]] = [
    # user & org administration
    ("user:read", "View users in scope"),
    ("user:manage", "Create, update, deactivate users and assign roles"),
    ("company:read", "View company/client organizations"),
    ("company:manage", "Create and update client organizations"),
    ("admin:manage", "Administer org settings, targets, factors, integrations, logs"),
    # datasets & review workflow
    ("dataset:read", "View datasets and their data"),
    ("dataset:create", "Upload/create datasets and files"),
    ("dataset:update", "Edit dataset metadata"),
    ("dataset:review", "Review datasets, record review decisions, run assurance"),
    ("comment:read", "View review comments"),
    ("comment:create", "Add review comments"),
    # outputs
    ("report:read", "View reports"),
    ("report:generate", "Generate ESG reports"),
    ("benchmark:read", "View benchmarking and analytics"),
    ("assessment:manage", "Create and manage assessments/surveys"),
    ("assessment:respond", "Respond to assessments/surveys"),
    # --- Stage 4 (master data + datasets) ---
    ("site:read", "View sites"),
    ("site:manage", "Create, update, deactivate sites"),
    ("business_unit:read", "View business units"),
    ("business_unit:manage", "Create, update, deactivate business units"),
    ("department:read", "View departments"),
    ("department:manage", "Create, update, deactivate departments"),
    ("upload_type:read", "View upload type registry"),
    ("upload_type:manage", "Create/update upload types (admin)"),
    ("dataset:submit", "Submit a dataset version for review"),
    ("file:upload", "Upload files to a dataset version"),
    ("file:download", "Download files"),
    ("audit:read", "View audit logs"),
    ("integration:manage", "Configure integrations"),
    ("notification:read", "Read own notifications"),


]

# --- Roles (code, scope, description) --------------------------------------
ROLES: list[tuple[str, str, str]] = [
    # Deloitte-side roles
    ("Administrator", "deloitte", "Full platform administration for Deloitte staff"),
    ("Consultant", "deloitte", "Manages assigned client engagements"),
    ("Reviewer", "deloitte", "Reviews and assures client-submitted data"),
    ("Support", "deloitte", "Read-only support access"),
    # Client-side roles
    ("Client Administrator", "client", "Administers their own company's users and settings"),
    ("Client Reviewer", "client", "Reviews their company's datasets"),
    ("Client Uploader", "client", "Uploads their company's datasets"),
    ("Client Approver", "client", "Approves their company's submissions"),
]

# --- Role -> permission mappings -------------------------------------------
# Kept explicit (no wildcards) so every grant is auditable.
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "Administrator": [
        "user:read", "user:manage", "company:read", "company:manage", "admin:manage",
        "dataset:read", "dataset:create", "dataset:update", "dataset:review",
        "comment:read", "comment:create",
        "report:read", "report:generate", "benchmark:read",
        "assessment:manage", "assessment:respond",
        "site:read", "site:manage", "business_unit:read", "business_unit:manage", "department:read", "department:manage", "upload_type:read", "upload_type:manage", "dataset:submit", "file:upload", "file:download", "audit:read", "integration:manage",
        "notification:read"],
    "Consultant": [
        "company:read", "user:read",
        "dataset:read", "dataset:create", "dataset:update", "dataset:review",
        "comment:read", "comment:create",
        "report:read", "report:generate", "benchmark:read",
        "assessment:manage",
        "site:read", "business_unit:read", "department:read", "upload_type:read", "dataset:submit", "file:upload", "file:download",
        "notification:read"],
    "Reviewer": [
        "company:read",
        "dataset:read", "dataset:review",
        "comment:read", "comment:create",
        "report:read", "benchmark:read",
        "site:read", "business_unit:read", "department:read", "upload_type:read", "file:download",
        "notification:read"],
    "Support": [
        "company:read", "user:read", "dataset:read", "comment:read", "report:read",
        "site:read", "business_unit:read", "department:read", "upload_type:read",
        "notification:read"],
    "Client Administrator": [
        "company:read", "user:read", "user:manage", "admin:manage",
        "dataset:read", "dataset:create", "dataset:update",
        "comment:read", "comment:create",
        "report:read", "report:generate", "benchmark:read",
        "assessment:manage", "assessment:respond",
        "site:read", "site:manage", "business_unit:read", "business_unit:manage", "department:read", "department:manage", "upload_type:read", "dataset:submit", "file:upload", "file:download", "audit:read",
        "notification:read"],
    "Client Reviewer": [
        "company:read", "dataset:read", "dataset:review", "comment:read", "comment:create",
        "report:read", "benchmark:read", "assessment:respond",
        "site:read", "business_unit:read", "department:read", "upload_type:read", "file:download",
        "notification:read"],
    "Client Uploader": [
        "company:read", "dataset:read", "dataset:create", "comment:read", "comment:create",
        "assessment:respond",
        "site:read", "business_unit:read", "department:read", "upload_type:read", "dataset:submit", "file:upload", "file:download",
        "notification:read"],
    "Client Approver": [
        "company:read", "dataset:read", "dataset:review", "comment:read", "comment:create",
        "report:read", "assessment:respond",
        "site:read", "business_unit:read", "department:read", "upload_type:read", "file:download",
        "notification:read"],
}
