import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import api


class PrivateApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.env = patch.dict(os.environ, {
            "JWT_SECRET": "test-secret-that-is-long-enough-to-use-in-unit-tests",
            "ADMIN_BOOTSTRAP_NAME": "Test Admin",
            "ADMIN_BOOTSTRAP_PHONE": "+967700000001",
            "ADMIN_BOOTSTRAP_PASSWORD": "long-test-admin-password",
        })
        self.env.start()
        self.db_patch = patch.object(api, "DB_PATH", self.db_path)
        self.db_patch.start()
        api._initialize_database()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.db_patch.stop()
        self.env.stop()
        self.temp_dir.cleanup()

    def test_registration_is_pending_and_case_routes_require_auth(self):
        response = self.client.post("/api/auth/register", json={
            "name": "Test User", "phone": "+967700000002", "password": "long-user-password"
        })
        self.assertEqual(response.status_code, 202)
        login = self.client.post("/api/auth/login", json={
            "phone": "+967700000002", "password": "long-user-password"
        })
        self.assertEqual(login.status_code, 403)
        self.assertEqual(self.client.get("/api/case-types").status_code, 401)

    def test_admin_can_approve_and_user_can_login(self):
        self.client.post("/api/auth/register", json={
            "name": "Test User", "phone": "+967700000002", "password": "long-user-password"
        })
        admin_login = self.client.post("/api/auth/login", json={
            "phone": "+967700000001", "password": "long-test-admin-password"
        }).json()
        headers = {"Authorization": f"Bearer {admin_login['access_token']}"}
        users = self.client.get("/api/admin/users", headers=headers).json()["users"]
        self.assertEqual(len(users), 1)
        decision = self.client.post(
            f"/api/admin/users/{users[0]['id']}/decision",
            headers=headers,
            json={"approve": True},
        )
        self.assertEqual(decision.json()["status"], "approved")
        login = self.client.post("/api/auth/login", json={
            "phone": "+967700000002", "password": "long-user-password"
        })
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["user"]["role"], "user")


if __name__ == "__main__":
    unittest.main()
