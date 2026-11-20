"""Regression coverage for the legacy /api/v1/models NameError guard.

This test intentionally uses a tiny router double so the unit-test phase can
run before the CI workflow installs the application requirements.
"""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.saas_api import _install_models_route_guard


class FakeHTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FakeRoute:
    def __init__(self, path, endpoint):
        self.path = path
        self.endpoint = endpoint


class FakeApp:
    def __init__(self):
        self.router = SimpleNamespace(routes=[])

    def get(self, path):
        def register(endpoint):
            self.router.routes.append(FakeRoute(path, endpoint))
            return endpoint
        return register


class ModelsRouteGuardTests(unittest.TestCase):
    def test_controlled_503_precedes_broken_legacy_handler(self):
        app = FakeApp()

        def broken_legacy_handler():
            raise NameError("name 'list_models' is not defined")

        app.router.routes.append(FakeRoute("/api/v1/models", broken_legacy_handler))

        with patch.dict(sys.modules, {"fastapi": SimpleNamespace(HTTPException=FakeHTTPException)}):
            _install_models_route_guard(app)

        self.assertEqual(len(app.router.routes), 2)
        self.assertEqual(app.router.routes[0].path, "/api/v1/models")
        self.assertIsNot(app.router.routes[0].endpoint, broken_legacy_handler)

        with self.assertRaises(FakeHTTPException) as raised:
            app.router.routes[0].endpoint()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "model_registry_unavailable")
        self.assertIn("temporarily unavailable", raised.exception.detail["message"])


    def test_fastapi_client_gets_controlled_503_before_broken_legacy_route(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI test dependencies are installed in the integration CI phase")

        app = FastAPI()

        @app.get("/api/v1/models")
        def broken_legacy_handler():
            raise NameError("name 'list_models' is not defined")

        _install_models_route_guard(app)
        response = TestClient(app).get("/api/v1/models")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "model_registry_unavailable")


if __name__ == "__main__":
    unittest.main()
