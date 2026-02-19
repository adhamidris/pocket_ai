from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import BusinessProfile, RegistrationSession
from apps.knowledge.models import KnowledgeDriftSample


User = get_user_model()


class DashboardRAGAnalyticsTests(TestCase):
    def setUp(self) -> None:
        self.url = reverse("frontend:dashboard-rag-analytics")

    def _create_business(self, *, email: str, name: str) -> BusinessProfile:
        user = User.objects.create_user(email=email, password="changeme123")
        registration = RegistrationSession.objects.create(user=user)
        return BusinessProfile.objects.create(
            user=user,
            registration_session=registration,
            name=name,
            industry="Retail",
        )

    def test_dashboard_rag_analytics_requires_login(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_dashboard_rag_analytics_returns_404_for_non_superuser(self) -> None:
        user = User.objects.create_user(email="member@example.com", password="changeme123")
        self.client.force_login(user)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_dashboard_rag_analytics_renders_report_for_superuser(self) -> None:
        superuser = User.objects.create_superuser(email="owner@example.com", password="changeme123")
        self.client.force_login(superuser)
        business = self._create_business(email="tenant@example.com", name="Acme Analytics")

        KnowledgeDriftSample.objects.create(
            business_profile=business,
            sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
            metrics={"status": "ok", "result_count": 2, "query_intent": "lookup", "stage": "hybrid", "latency_ms": 120},
        )
        KnowledgeDriftSample.objects.create(
            business_profile=business,
            sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
            metrics={
                "status": "not_found",
                "result_count": 0,
                "query_intent": "lookup",
                "stage": "fallback",
                "error_code": "not_found",
                "latency_ms": 180,
            },
        )
        KnowledgeDriftSample.objects.create(
            business_profile=business,
            sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
            metrics={
                "status": "throttled",
                "result_count": 0,
                "query_intent": "invoice_id",
                "stage": "alias_exact",
                "error_code": "rate_limited",
                "latency_ms": 95,
            },
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RAG Query Analytics")
        self.assertContains(response, "Acme Analytics")

        report = response.context["query_analytics"]
        self.assertEqual(report["total_queries"], 3)
        self.assertEqual((report["status"]["counts"] or {}).get("ok"), 1)
        self.assertEqual((report["status"]["counts"] or {}).get("not_found"), 1)
        self.assertEqual((report["status"]["counts"] or {}).get("throttled"), 1)

    def test_dashboard_rag_analytics_applies_business_filter(self) -> None:
        superuser = User.objects.create_superuser(email="owner2@example.com", password="changeme123")
        self.client.force_login(superuser)
        first = self._create_business(email="tenant-a@example.com", name="Tenant A")
        second = self._create_business(email="tenant-b@example.com", name="Tenant B")

        KnowledgeDriftSample.objects.create(
            business_profile=first,
            sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
            metrics={"status": "ok", "result_count": 1, "latency_ms": 80},
        )
        KnowledgeDriftSample.objects.create(
            business_profile=second,
            sample_kind=KnowledgeDriftSample.SampleKind.RETRIEVAL,
            metrics={"status": "error", "result_count": 0, "latency_ms": 160},
        )

        response = self.client.get(self.url, {"business_id": str(first.id), "hours": "24"})
        self.assertEqual(response.status_code, 200)
        report = response.context["query_analytics"]
        self.assertEqual(report["total_queries"], 1)
        self.assertEqual((report["status"]["counts"] or {}).get("ok"), 1)

