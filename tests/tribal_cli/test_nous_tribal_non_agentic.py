"""Tests for the Nous-Tribal-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"tribal"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``tribal-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "tribal" tag namespace.

``is_nous_tribal_non_agentic`` should only match the actual Nous Research
Tribal-3 / Tribal-4 chat family.
"""

from __future__ import annotations

import pytest

from tribal_cli.model_switch import (
    _TRIBAL_MODEL_WARNING,
    _check_tribal_model_warning,
    is_nous_tribal_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "NousResearch/Tribal-3-Llama-3.1-70B",
        "NousResearch/Tribal-3-Llama-3.1-405B",
        "tribal-3",
        "Tribal-3",
        "tribal-4",
        "tribal-4-405b",
        "tribal_4_70b",
        "openrouter/tribal3:70b",
        "openrouter/nousresearch/tribal-4-405b",
        "NousResearch/Tribal3",
        "tribal-3.1",
    ],
)
def test_matches_real_nous_tribal_chat_models(model_name: str) -> None:
    assert is_nous_tribal_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous Tribal 3/4"
    )
    assert _check_tribal_model_warning(model_name) == _TRIBAL_MODEL_WARNING


@pytest.mark.parametrize(
    "model_name",
    [
        # Kyle's local Modelfile — qwen3:14b under a custom tag
        "tribal-brain:qwen3-14b-ctx16k",
        "tribal-brain:qwen3-14b-ctx32k",
        "tribal-honcho:qwen3-8b-ctx8k",
        # Plain unrelated models
        "qwen3:14b",
        "qwen3-coder:30b",
        "qwen2.5:14b",
        "claude-opus-4-6",
        "anthropic/claude-sonnet-4.5",
        "gpt-5",
        "openai/gpt-4o",
        "google/gemini-2.5-flash",
        "deepseek-chat",
        # Non-chat Tribal models we don't warn about
        "tribal-llm-2",
        "tribal2-pro",
        "nous-tribal-2-mistral",
        # Edge cases
        "",
        "tribal",  # bare "tribal" isn't the 3/4 family
        "tribal-brain",
        "brain-tribal-3-impostor",  # "3" not preceded by /: boundary
    ],
)
def test_does_not_match_unrelated_models(model_name: str) -> None:
    assert not is_nous_tribal_non_agentic(model_name), (
        f"expected {model_name!r} NOT to be flagged as Nous Tribal 3/4"
    )
    assert _check_tribal_model_warning(model_name) == ""


def test_none_like_inputs_are_safe() -> None:
    assert is_nous_tribal_non_agentic("") is False
    # Defensive: the helper shouldn't crash on None-ish falsy input either.
    assert _check_tribal_model_warning("") == ""
