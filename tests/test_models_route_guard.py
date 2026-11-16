"""Regression coverage for the legacy /api/v1/models NameError guard."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.saas_api import _install_models_route_guard


def test_models_route_returns_controlled_503_before_legacy_handler():
    app = FastAPI()

    @app.get("/api/v1/models")
    def broken_legacy_handler():
        # Mirrors the deployed route's undefined-helper failure without relying
        # on the packaged ZIP's internal source layout.
        raise NameError("name 'list_models' is not defined")

    _install_models_route_guard(app)

    response = TestClient(app).get("/api/v1/models")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "model_registry_unavailable"
    assert "temporarily unavailable" in response.json()["detail"]["message"]
