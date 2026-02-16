from __future__ import annotations

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import BusinessProfile, RegistrationSession
from frontend.context_processors import site_globals


User = get_user_model()


class LanguageSwitchingPhase7Tests(TestCase):
    _HTML_LANG_DIR_PATTERN = re.compile(r'<html lang="([^"]+)" dir="(ltr|rtl)">')

    def _assert_root_language(self, response, *, lang_prefix: str, direction: str) -> None:
        html = response.content.decode("utf-8")
        match = self._HTML_LANG_DIR_PATTERN.search(html)
        self.assertIsNotNone(match, msg="Expected root <html lang=... dir=...> in response HTML.")
        assert match is not None
        self.assertTrue(match.group(1).lower().startswith(lang_prefix))
        self.assertEqual(match.group(2), direction)

    def test_set_language_endpoint_sets_cookie_and_renders_rtl(self) -> None:
        login_url = reverse("accounts:login")
        response = self.client.post(
            reverse("set_language"),
            data={"language": "ar", "next": login_url},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], login_url)
        self.assertIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "ar")

        follow_up = self.client.get(login_url)
        self._assert_root_language(follow_up, lang_prefix="ar", direction="rtl")

    def test_query_param_switches_language_instantly_and_persists(self) -> None:
        login_url = reverse("accounts:login")

        to_ar = self.client.get(f"{login_url}?lang=ar")
        self.assertEqual(to_ar.status_code, 200)
        self.assertIn(settings.LANGUAGE_COOKIE_NAME, to_ar.cookies)
        self.assertEqual(to_ar.cookies[settings.LANGUAGE_COOKIE_NAME].value, "ar")
        self._assert_root_language(to_ar, lang_prefix="ar", direction="rtl")

        to_en = self.client.get(f"{login_url}?lang=en")
        self.assertEqual(to_en.status_code, 200)
        self.assertIn(settings.LANGUAGE_COOKIE_NAME, to_en.cookies)
        self.assertEqual(to_en.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en")
        self._assert_root_language(to_en, lang_prefix="en", direction="ltr")

        refreshed = self.client.get(login_url)
        self._assert_root_language(refreshed, lang_prefix="en", direction="ltr")

    def test_authenticated_query_param_persists_business_language(self) -> None:
        user = User.objects.create_user(email="phase7-i18n@example.com", password="changeme123")
        registration = RegistrationSession.objects.create(user=user)
        business = BusinessProfile.objects.create(
            user=user,
            registration_session=registration,
            name="Phase 7 Co",
            industry="Retail",
        )
        self.client.force_login(user)

        dashboard_url = reverse("frontend:dashboard")
        response = self.client.get(f"{dashboard_url}?lang=ar")

        self.assertEqual(response.status_code, 200)
        self.assertIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "ar")
        self._assert_root_language(response, lang_prefix="ar", direction="rtl")

        business.refresh_from_db()
        metadata = business.metadata if isinstance(business.metadata, dict) else {}
        preferences = metadata.get("preferences") if isinstance(metadata.get("preferences"), dict) else {}
        self.assertEqual(preferences.get("language"), "ar")

        refreshed = self.client.get(dashboard_url)
        self._assert_root_language(refreshed, lang_prefix="ar", direction="rtl")

    def test_authenticated_profile_language_bootstraps_cookie_without_query_param(self) -> None:
        user = User.objects.create_user(email="phase7-profile-lang@example.com", password="changeme123")
        registration = RegistrationSession.objects.create(user=user)
        BusinessProfile.objects.create(
            user=user,
            registration_session=registration,
            name="Profile Language Co",
            industry="Retail",
            metadata={"preferences": {"language": "ar"}},
        )
        self.client.force_login(user)

        response = self.client.get(reverse("frontend:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "ar")
        self._assert_root_language(response, lang_prefix="ar", direction="rtl")

    @override_settings(LANGUAGES=[("en", "English")])
    def test_unsupported_query_language_is_ignored_when_arabic_is_disabled(self) -> None:
        login_url = reverse("accounts:login")
        response = self.client.get(f"{login_url}?lang=ar")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)
        self._assert_root_language(response, lang_prefix="en", direction="ltr")


class SiteGlobalsLanguageToggleTests(SimpleTestCase):
    @override_settings(LANGUAGES=[("en", "English")])
    def test_language_toggle_options_follow_active_settings_languages(self) -> None:
        request = RequestFactory().get("/")
        payload = site_globals(request)
        options = payload["site"]["language_toggle"]["options"]
        self.assertEqual([item["code"] for item in options], ["en"])
