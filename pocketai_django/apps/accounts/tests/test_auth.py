from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


User = get_user_model()


class LoginViewTests(TestCase):
    def setUp(self) -> None:
        self.password = "Testpass123!"
        self.user = User.objects.create_user(
            email="tester@example.com",
            password=self.password,
            first_name="Tester",
        )

    def test_login_page_renders_for_get(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome back")

    def test_login_redirects_when_authenticated(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:login"), follow=True)
        self.assertRedirects(response, reverse("frontend:dashboard"))

    def test_login_with_json_payload(self):
        response = self.client.post(
            reverse("accounts:login"),
            data=json.dumps(
                {
                    "email": self.user.email,
                    "password": self.password,
                }
            ),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("status"), "ok")
        self.assertTrue(payload.get("redirect"))
        # Session should now authenticate the test client.
        self.assertTrue("_auth_user_id" in self.client.session)

    def test_login_rejects_invalid_credentials(self):
        response = self.client.post(
            reverse("accounts:login"),
            data=json.dumps(
                {
                    "email": self.user.email,
                    "password": "wrong-pass",
                }
            ),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload.get("error"), "INVALID_CREDENTIALS")
        self.assertNotIn("_auth_user_id", self.client.session)


class LogoutViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="logouter@example.com",
            password="AnotherPass123!",
            first_name="Logouter",
        )

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:logout"))
        self.assertEqual(response.status_code, 405)

    def test_logout_clears_session_with_json(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("accounts:logout"),
            data=json.dumps({"next": "/"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("status"), "ok")
        self.assertNotIn("_auth_user_id", self.client.session)
