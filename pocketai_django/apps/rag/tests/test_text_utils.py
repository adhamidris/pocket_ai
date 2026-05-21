"""Unit tests for the shared text-normalization utilities."""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.rag.lexicon.text_utils import PLURAL_BLACKLIST, is_plural_candidate, singularize


class TestSingularize(SimpleTestCase):
    """Tests for singularize()."""

    # -- critical regression: "plus" must survive unchanged --------------------
    def test_plus_unchanged(self):
        self.assertEqual(singularize("plus"), "plus")

    def test_versus_unchanged(self):
        self.assertEqual(singularize("versus"), "versus")

    def test_bonus_unchanged(self):
        self.assertEqual(singularize("bonus"), "bonus")

    def test_status_unchanged(self):
        self.assertEqual(singularize("status"), "status")

    # -- regular "s" removal --------------------------------------------------
    def test_fees_to_fee(self):
        self.assertEqual(singularize("fees"), "fee")

    def test_cards_to_card(self):
        self.assertEqual(singularize("cards"), "card")

    def test_products_to_product(self):
        self.assertEqual(singularize("products"), "product")

    def test_transfers_to_transfer(self):
        self.assertEqual(singularize("transfers"), "transfer")

    # -- "ies" → "y" ----------------------------------------------------------
    def test_batteries_to_battery(self):
        self.assertEqual(singularize("batteries"), "battery")

    def test_categories_to_category(self):
        self.assertEqual(singularize("categories"), "category")

    # -- "sses" → "ss" --------------------------------------------------------
    def test_dresses_to_dress(self):
        self.assertEqual(singularize("dresses"), "dress")

    # -- edge cases: short tokens left alone -----------------------------------
    def test_short_token_bus(self):
        self.assertEqual(singularize("bus"), "bus")

    def test_short_token_as(self):
        self.assertEqual(singularize("as"), "as")

    # -- double-s stays -------------------------------------------------------
    def test_moss_unchanged(self):
        self.assertEqual(singularize("moss"), "moss")

    def test_boss_unchanged(self):
        self.assertEqual(singularize("boss"), "boss")


class TestIsPluralCandidate(SimpleTestCase):
    """Tests for is_plural_candidate()."""

    def test_plus_not_candidate(self):
        self.assertFalse(is_plural_candidate("plus"))

    def test_bus_not_candidate(self):
        self.assertFalse(is_plural_candidate("bus"))

    def test_boss_not_candidate(self):
        self.assertFalse(is_plural_candidate("boss"))

    def test_fees_is_candidate(self):
        self.assertTrue(is_plural_candidate("fees"))

    def test_cards_is_candidate(self):
        self.assertTrue(is_plural_candidate("cards"))

    def test_batteries_is_candidate(self):
        self.assertTrue(is_plural_candidate("batteries"))

    def test_this_not_candidate(self):
        self.assertFalse(is_plural_candidate("this"))

    def test_does_not_candidate(self):
        self.assertFalse(is_plural_candidate("does"))


class TestPluralBlacklist(SimpleTestCase):
    """Ensure blacklist contains known safe-guard entries."""

    def test_plus_in_blacklist(self):
        self.assertIn("plus", PLURAL_BLACKLIST)

    def test_versus_in_blacklist(self):
        self.assertIn("versus", PLURAL_BLACKLIST)

    def test_bonus_in_blacklist(self):
        self.assertIn("bonus", PLURAL_BLACKLIST)

    def test_is_frozenset(self):
        self.assertIsInstance(PLURAL_BLACKLIST, frozenset)
