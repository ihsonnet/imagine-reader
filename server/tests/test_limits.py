"""Limits for a public deployment."""

from conftest import upload
from fastapi.testclient import TestClient

from imagine_reader_server.api import create_app
from imagine_reader_server.config import Settings
from imagine_reader_server.jobs import process_next


def app_with(tmp_path, **limits):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", **limits)
    return create_app(settings), settings


def test_uploads_per_visitor_are_limited(tmp_path):
    app, _ = app_with(tmp_path, uploads_per_hour=2)
    statuses = [upload(TestClient(app)).status_code for _ in range(3)]
    assert statuses == [201, 201, 429]


def test_the_client_address_header_counts_only_when_configured(tmp_path):
    trusting, _ = app_with(tmp_path / "a", uploads_per_hour=1, client_ip_header="cf-connecting-ip")
    first = TestClient(trusting, headers={"cf-connecting-ip": "203.0.113.1"})
    second = TestClient(trusting, headers={"cf-connecting-ip": "203.0.113.2"})
    assert [upload(first).status_code, upload(first).status_code, upload(second).status_code] == [201, 429, 201]

    # Without the setting, a forged header does not buy a fresh budget.
    ignoring, _ = app_with(tmp_path / "b", uploads_per_hour=1)
    forged = [TestClient(ignoring, headers={"cf-connecting-ip": f"203.0.113.{n}"}) for n in (1, 2)]
    assert [upload(client).status_code for client in forged] == [201, 429]


def test_api_requests_per_visitor_are_limited(tmp_path):
    app, _ = app_with(tmp_path, requests_per_minute=5)
    client = TestClient(app)
    statuses = [client.get("/api/books").status_code for _ in range(6)]
    assert statuses == [200] * 5 + [429]
    assert client.get("/api/books").headers["retry-after"] == "60"


def test_uploads_pause_when_the_site_is_full_or_busy(tmp_path):
    full, _ = app_with(tmp_path / "full", max_storage_mb=0)
    assert upload(TestClient(full)).status_code == 503

    busy, _ = app_with(tmp_path / "busy", max_queued_ingests=1)
    assert upload(TestClient(busy)).status_code == 201  # queued, not yet read
    assert upload(TestClient(busy)).status_code == 503


def test_preparation_requests_per_visitor_are_limited(tmp_path):
    app, settings = app_with(tmp_path, preparations_per_hour=2)
    client = TestClient(app)
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    body = {"section_id": "sec-001"}
    statuses = [
        client.post(f"/api/books/{book_id}/narration", json=body).status_code,
        client.post(f"/api/books/{book_id}/sketches", json=body).status_code,
        client.post(f"/api/books/{book_id}/narration", json=body).status_code,
    ]
    assert statuses == [202, 202, 429]


def test_pages_carry_security_headers(client):
    headers = client.get("/api/health").headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"
