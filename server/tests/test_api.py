from conftest import FIXTURES, upload
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from imagine_reader_server import storage
from imagine_reader_server.api import create_app
from imagine_reader_server.config import Settings
from imagine_reader_server.db import Job
from imagine_reader_server.jobs import claim_next, process_next, recover_interrupted


def test_upload_process_read_and_delete_a_book(client, app, settings):
    response = upload(client)
    assert response.status_code == 201
    book = response.json()
    assert book["status"] == "queued" and book["filename"] == "two-column.pdf" and book["mine"]
    assert not response.cookies  # no cookies: every book on this reader is yours
    assert client.get(f"/api/books/{book['id']}/reading").status_code == 409

    assert process_next(app.state.engine, settings)
    book = client.get(f"/api/books/{book['id']}").json()
    assert (book["status"], book["page_count"]) == ("ready", 2)
    assert book["word_count"] > 600

    reading = client.get(f"/api/books/{book['id']}/reading").json()
    assert [section["title"] for section in reading["sections"]] == ["The Lantern Keeper"]
    assert len(reading["paragraphs"]) == 22
    first = reading["sentences"][1]
    assert first["text"].startswith("Every evening")
    assert all(len(box) == 5 for box in first["boxes"])

    pdf = client.get(f"/api/books/{book['id']}/source.pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content == (FIXTURES / "two-column.pdf").read_bytes()
    assert [listed["id"] for listed in client.get("/api/books").json()] == [book["id"]]

    assert client.delete(f"/api/books/{book['id']}").status_code == 204
    assert client.get(f"/api/books/{book['id']}").status_code == 404
    assert not storage.book_dir(settings, book["id"]).exists()


def test_pages_carry_security_headers(client):
    headers = client.get("/api/health").headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"


def test_rejects_files_that_are_not_pdfs(client, settings):
    response = upload(client, "notes.pdf", b"PK\x03\x04 a zip archive")
    assert response.status_code == 415
    assert list((settings.data_dir / "books").iterdir()) == []


def test_rejects_uploads_over_the_size_limit(tmp_path):
    small = Settings(_env_file=None, data_dir=tmp_path / "data", max_upload_bytes=1000)
    response = upload(TestClient(create_app(small)))
    assert response.status_code == 413


def test_reports_books_over_the_page_limit(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", max_pages=1)
    app = create_app(settings)
    client = TestClient(app)
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    book = client.get(f"/api/books/{book_id}").json()
    assert (book["status"], book["error_code"]) == ("failed", "too_many_pages")
    assert "more pages than the limit" in book["error_message"]


def test_a_book_over_the_page_limit_can_be_trimmed_and_read(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", max_pages=1)
    app = create_app(settings)
    client = TestClient(app)
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    trimmed = client.post(f"/api/books/{book_id}/trim")
    assert trimmed.status_code == 202 and trimmed.json()["status"] == "queued"
    process_next(app.state.engine, settings)
    book = client.get(f"/api/books/{book_id}").json()
    assert (book["status"], book["page_count"], book["error_code"]) == ("ready", 1, None)
    reading = client.get(f"/api/books/{book_id}/reading").json()
    assert reading["warnings"][0].startswith("Only the first 1 of 2 pages were read")
    assert client.post(f"/api/books/{book_id}/trim").status_code == 409  # only a book that was too long


def test_a_scanned_book_is_refused_as_a_scan(client, app, settings):
    book_id = upload(client, "scanned-page.pdf").json()["id"]
    process_next(app.state.engine, settings)
    book = client.get(f"/api/books/{book_id}").json()
    assert (book["status"], book["error_code"]) == ("failed", "scanned_pdf")
    assert "is a scan" in book["error_message"]


def test_a_pdf_that_takes_too_long_is_stopped(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", job_timeout_seconds=0.05)
    app = create_app(settings)
    client = TestClient(app)
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    assert client.get(f"/api/books/{book_id}").json()["error_code"] == "timeout"


def test_jobs_interrupted_by_a_worker_restart_run_again(client, app, settings):
    book_id = upload(client).json()["id"]
    assert claim_next(app.state.engine) is not None  # the worker died after claiming it
    assert client.get(f"/api/books/{book_id}").json()["status"] == "processing"

    recover_interrupted(app.state.engine, settings)
    with Session(app.state.engine) as db:
        assert db.get(Job, 1).state == "queued"
    assert process_next(app.state.engine, settings)
    assert client.get(f"/api/books/{book_id}").json()["status"] == "ready"


def test_api_and_worker_can_start_together_on_a_new_database(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import inspect

    from imagine_reader_server.db import make_engine

    settings = Settings(_env_file=None, data_dir=tmp_path / "fresh")
    with ThreadPoolExecutor(4) as pool:
        engines = list(pool.map(lambda _: make_engine(settings), range(4)))
    assert {"books", "jobs", "narration_clips"} <= set(inspect(engines[0]).get_table_names())


def test_a_read_book_has_a_small_cover(client, app, settings):
    import io

    from PIL import Image

    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    book = client.get(f"/api/books/{book_id}").json()
    cover = client.get(book["cover_url"])
    assert cover.status_code == 200 and cover.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(cover.content)).size[0] == 360
    (storage.book_dir(settings, book_id) / storage.COVER).unlink()  # a book read before covers existed
    assert client.get(book["cover_url"]).status_code == 200
