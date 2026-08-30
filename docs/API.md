# API (through Stage 3)

Base: /api/v1 · Bearer auth · pagination {items,total,page,page_size} ·
errors {error:{code,message,field}}

## Companies (company:read / company:manage)
GET  /companies?page&page_size&search&status&industry&sort&order
POST /companies
GET  /companies/{id}
PATCH /companies/{id}

## Users (user:read / user:manage)
GET  /users?page&page_size&search&role&company_id
POST /users            (password hashed; role_code assigns RBAC role)
GET  /users/{id}
PATCH /users/{id}
DELETE /users/{id}      (soft delete)

## Consultant assignments (user:manage)
GET  /consultant-assignments?company_id&active_only
POST /consultant-assignments
PATCH /consultant-assignments/{id}

## Role assignment (user:manage)
POST   /rbac/assign    {user_id, role_code, company_id?}
DELETE /rbac/assign    {user_id, role_code, company_id?}
GET    /rbac/roles
GET    /rbac/me/permissions
