from pathlib import Path

from fastapi.testclient import TestClient

from tend_eval.config import Settings
from tend_eval.main import create_app


def test_root_and_defaults_do_not_expose_api_key(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        OPENAI_API_KEY="secret-value",
        TEND_SOURCE_DIR=tmp_path / "upstream",
        TEND_RELEASE_DIR=tmp_path / "release",
        TEND_EVAL_RUNTIME_DIR=tmp_path / "runtime",
    )
    with TestClient(create_app(settings)) as client:
        root = client.get("/")
        defaults = client.get("/api/config/defaults")
    assert root.status_code == 200
    assert root.json()["name"] == "TEND Evaluation System"
    assert defaults.status_code == 200
    assert defaults.json()["model"] == "gpt-5.6-luna"
    assert defaults.json()["api_key_configured"] is True
    assert "secret-value" not in defaults.text


def test_catalog_is_available_without_local_release(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        TEND_SOURCE_DIR=tmp_path / "upstream",
        TEND_RELEASE_DIR=tmp_path / "release",
        TEND_EVAL_RUNTIME_DIR=tmp_path / "runtime",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/catalog")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["methods"]) == 10
    assert payload["dataset"]["available"] is False

