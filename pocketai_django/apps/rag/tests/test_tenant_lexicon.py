from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase

from apps.accounts.models import BusinessProfile, RegistrationSession, User
from apps.knowledge.models import KnowledgeLexiconSynonym, KnowledgeLexiconTerm
from apps.rag.tenant_lexicon import TenantLexiconService, normalize_lexicon_text, tokenize_lexicon_text


class TenantLexiconServiceTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.user = User.objects.create(email="lexicon@example.com", first_name="Lex")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Lexicon Co",
            industry="operations",
        )

        self.other_user = User.objects.create(email="other-lexicon@example.com", first_name="Other")
        self.other_registration = RegistrationSession.objects.create(user=self.other_user)
        self.other_business = BusinessProfile.objects.create(
            user=self.other_user,
            registration_session=self.other_registration,
            name="Other Co",
            industry="healthcare",
        )
        self.service = TenantLexiconService(cache_ttl=300)

    def test_upsert_term_creates_canonical_with_synonyms(self) -> None:
        term = self.service.upsert_term(
            business_profile=self.business,
            term_type=KnowledgeLexiconTerm.TermType.ENTITY,
            canonical_text="Support Tickets",
            language_code="en",
            confidence_score=0.9,
            source="manual",
            synonyms=["service requests", "cases"],
        )

        self.assertEqual(term.canonical_normalized, "support tickets")
        self.assertEqual(term.language_code, "en")
        self.assertEqual(
            KnowledgeLexiconSynonym.objects.filter(term=term, business_profile=self.business).count(),
            2,
        )

        snapshot = self.service.get_snapshot(business_profile=self.business)
        self.assertIn("support tickets", snapshot.get("entity_terms", ()))
        self.assertIn("service requests", snapshot.get("entity_terms", ()))
        self.assertIn("cases", snapshot.get("entity_terms", ()))

    def test_upsert_term_is_idempotent_for_same_normalized_value(self) -> None:
        first = self.service.upsert_term(
            business_profile=self.business,
            term_type=KnowledgeLexiconTerm.TermType.ATTRIBUTE,
            canonical_text="Resolution   Time",
            language_code="en",
            confidence_score=0.5,
            source="manual",
        )
        second = self.service.upsert_term(
            business_profile=self.business,
            term_type=KnowledgeLexiconTerm.TermType.ATTRIBUTE,
            canonical_text="resolution time",
            language_code="en",
            confidence_score=0.8,
            source="ingestion",
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(
            KnowledgeLexiconTerm.objects.filter(
                business_profile=self.business,
                term_type=KnowledgeLexiconTerm.TermType.ATTRIBUTE,
            ).count(),
            1,
        )
        refreshed = KnowledgeLexiconTerm.objects.get(id=first.id)
        self.assertEqual(refreshed.confidence_score, 0.8)
        self.assertEqual(refreshed.source, "ingestion")

    def test_snapshot_is_tenant_scoped(self) -> None:
        self.service.upsert_term(
            business_profile=self.business,
            term_type=KnowledgeLexiconTerm.TermType.ENTITY,
            canonical_text="purchase orders",
            language_code="en",
        )
        self.service.upsert_term(
            business_profile=self.other_business,
            term_type=KnowledgeLexiconTerm.TermType.ENTITY,
            canonical_text="insurance claims",
            language_code="en",
        )

        first_snapshot = self.service.get_snapshot(business_profile=self.business)
        second_snapshot = self.service.get_snapshot(business_profile=self.other_business)
        self.assertIn("purchase orders", first_snapshot.get("entity_terms", ()))
        self.assertNotIn("insurance claims", first_snapshot.get("entity_terms", ()))
        self.assertIn("insurance claims", second_snapshot.get("entity_terms", ()))

    def test_upsert_synonym_rejects_cross_tenant_term(self) -> None:
        other_term = self.service.upsert_term(
            business_profile=self.other_business,
            term_type=KnowledgeLexiconTerm.TermType.ENTITY,
            canonical_text="patient records",
            language_code="en",
        )

        with self.assertRaises(ValueError):
            self.service.upsert_synonym(
                business_profile=self.business,
                term=other_term,
                synonym_text="records",
            )

    def test_snapshot_cache_is_invalidated_on_upsert(self) -> None:
        self.service.upsert_term(
            business_profile=self.business,
            term_type=KnowledgeLexiconTerm.TermType.ENTITY,
            canonical_text="tickets",
            language_code="en",
        )
        before = self.service.get_snapshot(business_profile=self.business, use_cache=True)
        self.assertIn("tickets", before.get("entity_terms", ()))

        self.service.upsert_term(
            business_profile=self.business,
            term_type=KnowledgeLexiconTerm.TermType.ENTITY,
            canonical_text="escalations",
            language_code="en",
        )
        after = self.service.get_snapshot(business_profile=self.business, use_cache=True)
        self.assertIn("tickets", after.get("entity_terms", ()))
        self.assertIn("escalations", after.get("entity_terms", ()))

    def test_arabic_normalization_collapses_script_variants(self) -> None:
        raw = "إِجْمالِيّ المُبيـعات ٢٠٢٥"
        self.assertEqual(normalize_lexicon_text(raw), "اجمالي المبيعات 2025")

    def test_tokenizer_keeps_order_and_can_optionally_dedupe(self) -> None:
        raw = "طلبات طلبات ١٢٣"
        self.assertEqual(tokenize_lexicon_text(raw), ("طلبات", "طلبات", "123"))
        self.assertEqual(tokenize_lexicon_text(raw, dedupe=True), ("طلبات", "123"))

    def test_snapshot_drops_corrupted_cross_tenant_synonym_rows(self) -> None:
        source_term = self.service.upsert_term(
            business_profile=self.business,
            term_type=KnowledgeLexiconTerm.TermType.ENTITY,
            canonical_text="purchase orders",
            language_code="en",
        )
        target_term = self.service.upsert_term(
            business_profile=self.other_business,
            term_type=KnowledgeLexiconTerm.TermType.ENTITY,
            canonical_text="insurance claims",
            language_code="en",
        )
        synonym = self.service.upsert_synonym(
            business_profile=self.business,
            term=source_term,
            synonym_text="po",
            language_code="en",
        )

        # Simulate DB inconsistency by bypassing model save validation.
        KnowledgeLexiconSynonym.objects.filter(id=synonym.id).update(term=target_term)

        snapshot = self.service.get_snapshot(business_profile=self.business, use_cache=False)
        self.assertIn("purchase orders", snapshot.get("entity_terms", ()))
        self.assertNotIn("po", snapshot.get("entity_terms", ()))
