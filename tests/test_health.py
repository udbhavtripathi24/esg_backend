def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "esg-platform-api"


def test_health_liveness(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_probe_requires_migrations_at_head(client, session):
    # Test tables come from the models with no alembic_version row: an
    # instance must not take traffic against a schema Alembic didn't build.
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["migrations"] == "pending" and r.json()["database"] == "ok"

    from sqlmodel import text
    from app.db.session import alembic_heads
    session.exec(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
    for head in alembic_heads():
        session.exec(text("INSERT INTO alembic_version VALUES (:v)").bindparams(v=head))
    session.commit()
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "database": "ok", "migrations": "up_to_date"}


def test_auto_create_is_never_honoured_in_production(monkeypatch):
    from app.db import session as db
    called = []
    monkeypatch.setattr(db.SQLModel.metadata, "create_all", lambda *a, **k: called.append(1))
    monkeypatch.setattr(db, "migration_state", lambda conn=None: {"current": [], "head": [], "up_to_date": True})
    monkeypatch.setattr(db.settings, "DB_AUTO_CREATE", True)
    monkeypatch.setattr(db.settings, "ENVIRONMENT", "production")
    db.init_db()
    assert called == []
    monkeypatch.setattr(db.settings, "ENVIRONMENT", "development")
    db.init_db()
    assert called == [1]


def test_request_id_header(client):
    r = client.get("/health")
    assert "X-Request-ID" in r.headers
    assert len(r.headers["X-Request-ID"]) > 0


def test_error_contract_on_404(client):
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body
    assert set(body["error"].keys()) == {"code", "message", "field"}
