# Deloitte Vista — Backend

Enterprise-grade ESG (Environmental, Social, Governance) data management platform backend built with FastAPI, SQLAlchemy, and PostgreSQL.

## Overview

The Deloitte Vista backend is a multi-tenant SaaS platform that enables organizations to collect, validate, review, and report on ESG data across multiple companies, sites, and facilities. It includes:

- **Multi-tenant architecture** with organization and company isolation
- **Role-based access control (RBAC)** with 8 roles and 30 permissions
- **Dataset management** with versioning and file uploads
- **Review workflow** with approval/rejection and threaded comments
- **Audit logging** for compliance and traceability
- **Notification system** with transactional outbox pattern
- **Background job processing** for data validation and KPI calculation
- **Master data management** for sites, facilities, and upload types
- **RESTful API** with OpenAPI/Swagger documentation

## Current Stage: Stage 5 (Review Workflow)

The platform has been built incrementally through 5 stages:

1. **Stage 1**: Foundation (FastAPI, PostgreSQL, basic auth)
2. **Stage 2**: RBAC and base tables
3. **Stage 3**: Companies, users, organizations, consultant assignments
4. **Stage 4**: ESG data foundation (datasets, files, master data, audit logs)
5. **Stage 5**: Review workflow (assignments, decisions, comments, notifications)

## Architecture

### Tech Stack

- **Framework**: FastAPI 0.115.6
- **Database**: PostgreSQL 16 with SQLAlchemy 2.0.36 and SQLModel
- **Authentication**: JWT tokens with python-jose
- **Validation**: Pydantic 2.13.5
- **Migrations**: Alembic 1.14.0
- **File Processing**: openpyxl for Excel files
- **Storage**: Pluggable storage backend (local filesystem or GCS)
- **Testing**: pytest with 99 tests
- **Logging**: structlog

### Key Features

#### Authentication & Authorization
- JWT-based authentication
- Password hashing with bcrypt
- Role-based access control with granular permissions
- Tenant isolation (organization-level and company-level)

#### Data Management
- **Datasets**: Versioned collections of ESG data
- **Dataset Versions**: Immutable snapshots with status workflow
- **Files**: Uploaded data files with validation and checksum verification
- **Master Data**: Sites, facilities, upload types
- **Audit Logs**: Complete audit trail of all actions

#### Review Workflow
- **Review Assignments**: Multi-tier reviewer assignments
- **Review Decisions**: Approve, request changes, or reject
- **Comments**: Threaded discussion on dataset versions
- **Segregation of Duties**: Prevents self-approval
- **Notifications**: Real-time notifications for assignments and decisions

#### Background Processing
- Job queue for asynchronous processing
- File validation and checksum calculation
- KPI recalculation on approval
- Transactional outbox for notifications

## Getting Started

### Prerequisites

- Python 3.12.7 (managed via pyenv)
- PostgreSQL 16
- Git

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/udbhavtripathi24/esg_backend.git
   cd esg_backend
   ```

2. **Set up Python 3.12.7 with pyenv**
   ```bash
   pyenv install 3.12.7
   pyenv local 3.12.7
   ```

3. **Create and activate virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

4. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install "bcrypt==4.0.1"
   ```

5. **Set up PostgreSQL database**
   ```bash
   # Create database user
   psql -U postgres <<SQL
   CREATE USER esg WITH PASSWORD 'esg' SUPERUSER;
   CREATE DATABASE esg_platform OWNER esg;
   SQL
   ```

6. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

7. **Run database migrations**
   ```bash
   export DATABASE_URL="postgresql+psycopg2://esg:esg@localhost:5432/esg_platform"
   alembic upgrade head
   ```

8. **Seed RBAC permissions and roles**
   ```bash
   python scripts_seed_rbac.py
   python scripts_seed_upload_types.py
   ```

### Running the Application

**Start the API server**
```bash
export DATABASE_URL="postgresql+psycopg2://esg:esg@localhost:5432/esg_platform"
export PYTHONPATH=$(pwd)
uvicorn app.main:app --reload
```

The API will be available at http://localhost:8000

**Access API documentation**
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

**Start the background worker** (optional, in a separate terminal)
```bash
python -m app.workers.runner
```

### Running Tests

```bash
export DATABASE_URL="postgresql+psycopg2://esg:esg@localhost:5432/esg_platform"
export PYTHONPATH=$(pwd)
pytest
```

Expected: **99 tests passing**

### End-to-End Verification

Stage-specific E2E tests are available:

```bash
# Stage 4 verification
python e2e_stage4.py

# Stage 5 verification
python e2e_stage5.py
```

## API Endpoints

### Authentication
- `POST /api/v1/auth/login` - Login with email/password
- `POST /api/v1/auth/refresh` - Refresh access token

### Companies
- `GET /api/v1/companies` - List companies
- `POST /api/v1/companies` - Create company
- `GET /api/v1/companies/{id}` - Get company details
- `PATCH /api/v1/companies/{id}` - Update company

### Users
- `GET /api/v1/users` - List users
- `POST /api/v1/users` - Create user
- `GET /api/v1/users/{id}` - Get user details
- `DELETE /api/v1/users/{id}` - Deactivate user

### Master Data
- `GET /api/v1/master-data/sites` - List sites
- `POST /api/v1/master-data/sites` - Create site
- `GET /api/v1/master-data/facilities` - List facilities
- `POST /api/v1/master-data/facilities` - Create facility

### Datasets
- `GET /api/v1/datasets` - List datasets
- `POST /api/v1/datasets` - Create dataset
- `GET /api/v1/datasets/{id}` - Get dataset details
- `POST /api/v1/datasets/{id}/versions` - Create new version
- `POST /api/v1/datasets/{ds_id}/versions/{v_id}/submit` - Submit for review
- `POST /api/v1/datasets/{ds_id}/versions/{v_id}/files` - Upload file
- `GET /api/v1/datasets/{ds_id}/versions/{v_id}/files/{file_id}/download` - Download file

### Reviews (Stage 5)
- `POST /api/v1/datasets/{ds_id}/versions/{v_id}/reviews` - Assign reviewer
- `GET /api/v1/datasets/{ds_id}/versions/{v_id}/reviews` - List reviews
- `POST /api/v1/datasets/{ds_id}/versions/{v_id}/reviews/{rv_id}/decide` - Make decision
- `POST /api/v1/datasets/{ds_id}/versions/{v_id}/comments` - Add comment
- `GET /api/v1/datasets/{ds_id}/versions/{v_id}/comments` - List comments

### Notifications (Stage 5)
- `GET /api/v1/notifications` - List user notifications
- `POST /api/v1/notifications/{id}/mark-read` - Mark notification as read
- `POST /api/v1/notifications/mark-all-read` - Mark all notifications as read

### Audit Logs
- `GET /api/v1/audit-logs` - Query audit logs

### RBAC
- `GET /api/v1/rbac/permissions` - List permissions
- `GET /api/v1/rbac/roles` - List roles
- `POST /api/v1/rbac/roles/{role_id}/permissions` - Assign permission to role

## Database Schema

The platform uses 23 tables across 5 stages:

**Core Tables**
- `organizations` - Multi-tenant organizations
- `companies` - Companies within organizations
- `users` - User accounts
- `consultant_assignments` - Consultant-company relationships

**RBAC Tables**
- `permissions` - System permissions (30 total)
- `roles` - User roles (8 total)
- `role_permissions` - Role-permission mappings (140 total)
- `user_roles` - User-role assignments

**Master Data Tables**
- `sites` - Physical locations
- `facilities` - Facilities within sites
- `upload_types` - Allowed file upload types (5 types)

**Dataset Tables**
- `datasets` - Dataset definitions
- `dataset_versions` - Versioned dataset submissions
- `dataset_files` - Uploaded files

**Review Tables (Stage 5)**
- `reviews` - Review assignments
- `review_comments` - Threaded comments
- `notifications` - User notifications
- `notification_outbox` - Transactional outbox for delivery

**System Tables**
- `audit_logs` - Audit trail
- `processing_jobs` - Background jobs
- `integrations` - External integrations
- `alembic_version` - Migration tracking

## Configuration

Key environment variables (see `.env.example`):

```bash
# Database
DATABASE_URL=postgresql+psycopg2://esg:esg@localhost:5432/esg_platform

# Security
SECRET_KEY=your-secret-key-here
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Environment
ENVIRONMENT=development

# Storage
STORAGE_BACKEND=local  # or 'gcs'
STORAGE_ROOT=/tmp/esg-uploads

# Logging
LOG_LEVEL=INFO
```

## Development

### Project Structure

```
esg-backend/
├── alembic/              # Database migrations
│   └── versions/         # Migration files
├── app/
│   ├── api/
│   │   ├── deps.py       # Dependencies (auth, DB session)
│   │   └── routes/       # API route handlers
│   ├── core/
│   │   ├── config.py     # Settings and configuration
│   │   ├── security.py   # JWT and password handling
│   │   ├── errors.py     # Error classes
│   │   └── logging.py    # Logging setup
│   ├── db/
│   │   └── session.py    # Database session management
│   ├── models/           # SQLModel database models
│   ├── rbac/             # RBAC definitions and service
│   ├── services/         # Business logic services
│   ├── storage/          # File storage backends
│   ├── workers/          # Background job workers
│   └── main.py           # FastAPI application entry point
├── tests/                # Test suite (99 tests)
├── scripts/              # Utility scripts
├── docs/                 # Documentation
├── requirements.txt      # Python dependencies
├── alembic.ini          # Alembic configuration
├── pytest.ini           # Pytest configuration
└── README.md            # This file
```

### Adding a New Feature

1. Create database models in `app/models/`
2. Generate migration: `alembic revision --autogenerate -m "description"`
3. Review and edit migration in `alembic/versions/`
4. Apply migration: `alembic upgrade head`
5. Add API routes in `app/api/routes/`
6. Add business logic in `app/services/`
7. Add tests in `tests/`
8. Update RBAC permissions if needed

### Code Style

- Follow PEP 8 guidelines
- Use type hints throughout
- Document functions with docstrings
- Keep functions focused and single-purpose
- Use SQLModel for database models
- Use Pydantic for request/response schemas

## Testing

The test suite includes:

- **Unit tests**: Individual component testing
- **Integration tests**: Database and API testing
- **E2E tests**: Complete workflow verification

**Test Coverage by Module:**
- Health checks: 1 test
- Companies: 7 tests
- Users: 6 tests
- Consultant assignments: 5 tests
- RBAC: 18 tests
- Storage: 2 tests
- Stage 4 (Data foundation): 41 tests
- Stage 5 (Review workflow): 23 tests
- Worker: 1 test

## Deployment

### Docker

```bash
docker build -t esg-backend .
docker run -p 8000:8000 -e DATABASE_URL=... esg-backend
```

### Production Checklist

- [ ] Set strong `SECRET_KEY`
- [ ] Configure production database with SSL
- [ ] Set `ENVIRONMENT=production`
- [ ] Enable HTTPS/TLS
- [ ] Configure CORS properly
- [ ] Set up database backups
- [ ] Configure log aggregation
- [ ] Set up monitoring and alerting
- [ ] Review and restrict RBAC permissions
- [ ] Enable rate limiting
- [ ] Configure file storage (GCS recommended)

## Monitoring & Observability

- Structured logging with structlog
- Request ID tracking
- Audit log for compliance
- Health check endpoint: `/health`

## Security

- JWT-based authentication
- Password hashing with bcrypt
- SQL injection prevention via SQLAlchemy
- Input validation with Pydantic
- RBAC with granular permissions
- Tenant isolation at database level
- Audit logging for all actions
- File upload validation
- Segregation of duties enforcement

## Troubleshooting

### Database Connection Issues

```bash
# Check PostgreSQL is running
pg_isready

# Test connection
psql -U esg -d esg_platform -c "SELECT version();"
```

### Migration Issues

```bash
# Check current migration
alembic current

# View migration history
alembic history

# Downgrade one revision (use with caution)
alembic downgrade -1
```

### Test Failures

```bash
# Run specific test file
pytest tests/test_stage5.py -v

# Run with debugging
pytest tests/test_stage5.py -v -s

# Run with coverage
pytest --cov=app tests/
```

## Contributing

1. Create a feature branch from `main`
2. Make your changes
3. Add tests for new functionality
4. Ensure all tests pass: `pytest`
5. Commit with descriptive message
6. Push to your branch
7. Create a pull request

## License

Proprietary - Deloitte

## Support

For issues or questions, contact the Vista development team.

---

**Current Version**: Stage 5 (Review Workflow)  
**Last Updated**: August 2026  
**Status**: ✅ 99 tests passing
