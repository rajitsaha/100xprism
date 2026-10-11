#!/usr/bin/env python3
"""
pricing.py — per-model $/1M-token rates and cost calculation.

Shared by token-dashboard.py (dashboard cost) and run-cost.py (pair-loop budget
checks) so both price sessions identically. Rates are matched by substring against
a lowercased model id, first match wins in RATES order — list more specific
patterns before broader ones that could also match (e.g. "gpt-5" before a
hypothetical bare "gpt"). Model ids matching nothing are priced at FALLBACK_KEY's
rates and counted separately so callers can flag what fraction of spend is an
estimate rather than a real per-model price.

Values below are standard API LIST PRICES in USD per 1M tokens. They are estimates
for local usage economics, not a reconstruction of subscription-plan billing.
"""

PRICING_AS_OF = "2026-10-11"
PRICING_SOURCES = {
    "anthropic": "https://www.anthropic.com/pricing",
    "openai": "https://developers.openai.com/api/docs/pricing",
}

_OPUS_4_5_PLUS = {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25}

# Most-specific patterns must come first. In particular, a broad `gpt-5` entry
# before `gpt-5.6` would silently price every newer model at the legacy rate.
# OpenAI rates are the short-context (<=272K input) tier.
RATES = [
    ("fable-5-1", {"input": 10.0, "output": 50.0, "cache_read": 0.25, "cache_write": 12.5}),
    ("fable-5", {"input": 10.0, "output": 50.0, "cache_read": 1.0, "cache_write": 12.5}),
    ("opus-5-5", {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0}),
    ("opus-5", _OPUS_4_5_PLUS),
    ("opus-4-8", _OPUS_4_5_PLUS),
    ("opus-4-7", _OPUS_4_5_PLUS),
    ("opus-4-6", _OPUS_4_5_PLUS),
    ("opus-4-5", _OPUS_4_5_PLUS),
    ("sonnet-5", {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}),
    ("opus-4", {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75}),
    ("sonnet", {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}),
    ("haiku-5-5", {"input": 0.1, "output": 0.5, "cache_read": 0.01, "cache_write": 0.125}),
    ("haiku-3-5", {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0}),
    ("haiku-3", {"input": 0.25, "output": 1.25, "cache_read": 0.03, "cache_write": 0.3}),
    ("haiku", {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25}),
    ("gpt-6-astra", {"input": 10.0, "output": 50.0, "cache_read": 1.0, "cache_write": 12.5}),
    ("gpt-6.1-sol", {"input": 2.0, "output": 10.0, "cache_read": 0.1, "cache_write": 2.5}),
    ("gpt-6-sol", {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}),
    ("gpt-6-luna", {"input": 0.1, "output": 0.5, "cache_read": 0.01, "cache_write": 0.125}),
    ("gpt-5.6-luna", {"input": 0.2, "output": 1.2, "cache_read": 0.02, "cache_write": 0.25}),
    ("gpt-5.6-terra", {"input": 2.0, "output": 12.0, "cache_read": 0.2, "cache_write": 2.5}),
    ("gpt-5.6-sol", {"input": 4.0, "output": 20.0, "cache_read": 0.4, "cache_write": 5.0}),
    ("gpt-5.6", {"input": 5.0, "output": 30.0, "cache_read": 0.5, "cache_write": 6.25}),
    ("gpt-5.3-codex", {"input": 1.75, "output": 14.0, "cache_read": 0.175, "cache_write": 1.75}),
    ("gpt-5.5", {"input": 5.0, "output": 30.0, "cache_read": 0.5, "cache_write": 6.25}),
    ("gpt-5.4-mini", {"input": 0.75, "output": 4.5, "cache_read": 0.075, "cache_write": 0.75}),
    ("gpt-5.4", {"input": 2.5, "output": 15.0, "cache_read": 0.25, "cache_write": 2.5}),
    ("gpt-5.2", {"input": 1.75, "output": 14.0, "cache_read": 0.175, "cache_write": 1.75}),
    ("gpt-5.1", {"input": 1.25, "output": 10.0, "cache_read": 0.125, "cache_write": 1.25}),
    ("gpt-5", {"input": 1.25, "output": 10.0, "cache_read": 0.125, "cache_write": 1.25}),
    ("o4", {"input": 10.0, "output": 40.0, "cache_read": 2.5, "cache_write": 10.0}),
    ("o3", {"input": 10.0, "output": 40.0, "cache_read": 2.5, "cache_write": 10.0}),
    ("gpt-4.1", {"input": 2.0, "output": 8.0, "cache_read": 0.5, "cache_write": 2.0}),
]
FALLBACK_KEY = "opus-4"
_FALLBACK_RATES = next(r for k, r in RATES if k == FALLBACK_KEY)

TOKEN_KEYS = ("input", "output", "cache_read", "cache_write")


def rates_for_model(model_id):
    """Return (rates_dict, is_fallback) for a model id. Unknown ids fall back to
    FALLBACK_KEY's rates and are flagged so callers can surface the estimate."""
    mid = (model_id or "").lower()
    for pattern, rates in RATES:
        if pattern in mid:
            return rates, False
    return _FALLBACK_RATES, True


def cost_of(tokens, model_id=None):
    """tokens: {input,output,cache_read,cache_write} -> dollars for one model."""
    rates, _ = rates_for_model(model_id)
    return sum(tokens.get(k, 0) / 1_000_000 * rates[k] for k in TOKEN_KEYS)


def cost_by_model(by_model):
    """by_model: {model_id: {input,output,cache_read,cache_write}}.
    Returns (total_cost, fallback_priced_tokens) — the latter is the token count
    priced at fallback rates because the model id matched no known pattern."""
    total = 0.0
    fallback_tokens = 0
    for model_id, tok in by_model.items():
        rates, is_fallback = rates_for_model(model_id)
        total += sum(tok.get(k, 0) / 1_000_000 * rates[k] for k in TOKEN_KEYS)
        if is_fallback:
            fallback_tokens += sum(tok.get(k, 0) for k in TOKEN_KEYS)
    return total, fallback_tokens


def cost_breakdown(by_model):
    """Return exact list-price dollars by token purpose for model-keyed usage."""
    out = {k: 0.0 for k in TOKEN_KEYS}
    for model_id, tok in by_model.items():
        rates, _ = rates_for_model(model_id)
        for key in TOKEN_KEYS:
            out[key] += tok.get(key, 0) / 1_000_000 * rates[key]
    return out
