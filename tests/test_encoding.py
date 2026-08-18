"""The console encoding fallback, and the prompt rules it protects."""

from __future__ import annotations

import pytest

from stock_ai.core.encoding import ERROR_HANDLER, SUBSTITUTIONS


def test_a_yen_sign_survives_a_cp932_console() -> None:
    """Observed live: "¥124.0 billion" printed as "\\xa5124.0 billion".

    cp932 has no U+00A5. It does have the fullwidth U+FFE5, so the currency
    has an equivalent the console can print - losing it in a tool about money
    is the one substitution that must not happen.
    """
    text = "revenue rose to ¥124.0 billion"
    rendered = text.encode("cp932", errors=ERROR_HANDLER).decode("cp932")

    assert "￥124.0" in rendered
    assert "\\xa5" not in rendered
    assert "?" not in rendered


def test_japanese_is_untouched_because_cp932_already_carries_it() -> None:
    """The fix must not "improve" the text that already worked."""
    text = "当社は本日、通期見通しを据え置きます。"
    assert text.encode("cp932", errors=ERROR_HANDLER).decode("cp932") == text


def test_a_character_with_no_equivalent_degrades_rather_than_escaping() -> None:
    """An escape sequence reads as data; a question mark reads as missing."""
    rendered = "gain 📈 today".encode("cp932", errors=ERROR_HANDLER).decode("cp932")
    assert rendered == "gain ? today"


def test_utf8_output_is_never_substituted() -> None:
    """On a modern terminal the handler is never reached at all."""
    text = "¥124.0 billion — 📈"
    assert text.encode("utf-8", errors=ERROR_HANDLER).decode("utf-8") == text


@pytest.mark.parametrize("original", sorted(SUBSTITUTIONS))
def test_every_substitute_is_itself_encodable(original: str) -> None:
    """A replacement the console also cannot encode fixes nothing."""
    SUBSTITUTIONS[original].encode("cp932")


def test_the_summary_prompt_pins_the_output_language() -> None:
    """The same Japanese excerpt came back in Japanese, then in English.

    A summary whose language is a coin flip cannot go in a notification.
    """
    from stock_ai.ai.analysis import SUMMARY_SYSTEM

    assert "same language" in SUMMARY_SYSTEM


def test_the_label_prompts_stay_english_because_the_parser_reads_english() -> None:
    """A Japanese "高" would be read as unclassifiable, not as high."""
    from stock_ai.ai.analysis import IMPORTANCE_SYSTEM, SENTIMENT_SYSTEM

    assert "same language" not in IMPORTANCE_SYSTEM
    assert "same language" not in SENTIMENT_SYSTEM
