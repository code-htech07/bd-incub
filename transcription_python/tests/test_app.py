import os
import unittest
from unittest.mock import patch

from app import create_app


class AppRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    def test_index_is_served(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Concours", response.data)

    def test_admin_route_rejects_unconfigured_token(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/admin_auth_check.php")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["status"], "error")

    def test_admin_route_accepts_configured_token(self):
        with patch.dict(os.environ, {"UPB_ADMIN_TOKEN": "example-token-123"}):
            response = self.client.post(
                "/admin_auth_check.php",
                headers={"Authorization": "Bearer example-token-123"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "success")

    def test_admin_options_request_does_not_require_token(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.options("/admin_auth_check.php")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "*",
        )


if __name__ == "__main__":
    unittest.main()
