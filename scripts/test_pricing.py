#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pricing


class TestPricing(unittest.TestCase):
    def test_known_model_not_flagged_fallback(self):
        rates, is_fallback = pricing.rates_for_model("claude-sonnet-4-5-20251001")
        self.assertFalse(is_fallback)
        self.assertEqual(rates, next(r for k, r in pricing.RATES if k == "sonnet"))

    def test_unknown_model_falls_back_and_is_flagged(self):
        rates, is_fallback = pricing.rates_for_model("some-future-model-xyz")
        self.assertTrue(is_fallback)
        self.assertEqual(rates, pricing._FALLBACK_RATES)

    def test_none_model_falls_back(self):
        rates, is_fallback = pricing.rates_for_model(None)
        self.assertTrue(is_fallback)

    def test_cost_of_uses_matched_model_rate(self):
        tokens = {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0}
        haiku_cost = pricing.cost_of(tokens, "claude-haiku-4-5-20251001")
        opus_cost = pricing.cost_of(tokens, "claude-opus-4-8")
        self.assertLess(haiku_cost, opus_cost)

    def test_cost_by_model_sums_across_models_and_flags_fallback_tokens(self):
        by_model = {
            "claude-sonnet-4-5": {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0},
            "totally-unknown-model": {"input": 0, "output": 500_000, "cache_read": 0, "cache_write": 0},
        }
        total, fallback_tokens = pricing.cost_by_model(by_model)
        sonnet_rates = next(r for k, r in pricing.RATES if k == "sonnet")
        expected_sonnet = 1_000_000 / 1_000_000 * sonnet_rates["input"]
        expected_fallback = 500_000 / 1_000_000 * pricing._FALLBACK_RATES["output"]
        self.assertAlmostEqual(total, expected_sonnet + expected_fallback, places=6)
        self.assertEqual(fallback_tokens, 500_000)

    def test_gpt_5_minor_uses_specific_rate(self):
        rates, is_fallback = pricing.rates_for_model("gpt-5.5-codex")
        self.assertFalse(is_fallback)
        self.assertEqual(rates, next(r for k, r in pricing.RATES if k == "gpt-5.5"))
        self.assertEqual(rates["cache_write"], 6.25)

    def test_current_anthropic_models_precede_family_fallbacks(self):
        opus, _ = pricing.rates_for_model("claude-opus-4-8")
        sonnet, _ = pricing.rates_for_model("claude-sonnet-5")
        self.assertEqual(opus["input"], 5.0)
        self.assertEqual(sonnet["input"], 2.0)

    def test_cost_breakdown_prices_each_purpose_by_model(self):
        by_model = {
            "gpt-5.5": {"input": 1_000_000, "output": 1_000_000,
                         "cache_read": 1_000_000, "cache_write": 0},
        }
        out = pricing.cost_breakdown(by_model)
        self.assertEqual(out["input"], 5.0)
        self.assertEqual(out["output"], 30.0)
        self.assertEqual(out["cache_read"], 0.5)

    def test_current_models_match_published_rates(self):
        expected = {
            "claude-opus-5-5": (4.0, 20.0, 0.2),
            "claude-opus-5": (5.0, 25.0, 0.5),
            "claude-opus-4-7": (5.0, 25.0, 0.5),
            "claude-opus-4-1": (15.0, 75.0, 1.5),
            "claude-fable-5-1": (10.0, 50.0, 0.25),
            "claude-fable-5": (10.0, 50.0, 1.0),
            "claude-sonnet-5-5": (2.0, 10.0, 0.2),
            "claude-haiku-5-5": (0.1, 0.5, 0.01),
            "claude-haiku-4-5-20251001": (1.0, 5.0, 0.1),
            "gpt-6-astra": (10.0, 50.0, 1.0),
            "gpt-6.1-sol": (2.0, 10.0, 0.1),
            "gpt-6-luna": (0.1, 0.5, 0.01),
            "gpt-5.6-sol": (4.0, 20.0, 0.4),
            "gpt-5.6-luna": (0.2, 1.2, 0.02),
            "gpt-5.6-terra": (2.0, 12.0, 0.2),
        }
        for model, (inp, out, read) in expected.items():
            rates, is_fallback = pricing.rates_for_model(model)
            self.assertFalse(is_fallback, model)
            self.assertEqual((rates["input"], rates["output"], rates["cache_read"]),
                             (inp, out, read), model)


if __name__ == "__main__":
    unittest.main()
