def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "esg-platform-api"


def test_health_liveness(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_probe(client):
    # DB reachable via the overridden SQLite session -> ready
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


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
