from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from imagine_reader_server.api import create_app
from imagine_reader_server.config import Settings

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "pdfs"


@pytest.fixture(autouse=True)
def no_real_ai(monkeypatch):
    """Tests never reach a real AI service, whatever the developer's environment holds."""
    for key in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(f"IR_{key}", raising=False)
    monkeypatch.setenv("IR_SAMPLES", "false")  # the samples have their own tests (test_sample.py)
    monkeypatch.setenv("IR_SAMPLE_PACK_URL", "")  # and never download a pack
    monkeypatch.setenv("IR_SKETCH_WORDS", "25")  # most tests count pictures at a fixed pace; Auto has its own tests


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Preparing the opening chapter after upload is tested on its own; elsewhere tests ask for what they need.
    return Settings(
        _env_file=None, data_dir=tmp_path / "data", reader_dist=tmp_path / "no-reader", prepare_on_upload=False
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def upload(client: TestClient, name: str = "two-column.pdf", data: bytes | None = None):
    content = (FIXTURES / name).read_bytes() if data is None else data
    return client.post("/api/books", files={"file": (name, content, "application/pdf")})
