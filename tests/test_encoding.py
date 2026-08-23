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


def _repo_root():
    """The project root, found from this file rather than the cwd."""
    import pathlib

    return pathlib.Path(__file__).resolve().parent.parent


def _non_ascii_lines(path) -> list[int]:
    """Line numbers of ``path`` that contain a byte above 0x7F."""
    raw = path.read_bytes()
    body = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    return [n for n, line in enumerate(body.split(b"\n"), 1) if any(byte > 0x7F for byte in line)]


def test_batch_launchers_are_pure_ascii() -> None:
    """cmd.exe reads a .bat in the console codepage, not UTF-8.

    On a Japanese Windows that is cp932, so UTF-8 Japanese in a .bat arrives as
    mojibake - and mojibake can end an ``echo`` early, after which cmd tries to
    run the rest of the line as a command. Observed live:

        '...' は、内部コマンドまたは外部コマンドとして認識されていません。

    Every .bat in this repo is ASCII for that reason, and one that was not
    broke on the first double-click. The Japanese belongs in the .ps1, which
    is read as UTF-8 when it carries a BOM.
    """
    offenders = {
        path.name: _non_ascii_lines(path)
        for path in sorted(_repo_root().glob("*.bat"))
        if _non_ascii_lines(path)
    }
    assert not offenders, (
        f"non-ASCII in a .bat, which cmd.exe reads as cp932: {offenders}. "
        "Put the Japanese in the .ps1 instead."
    )


def test_powershell_scripts_with_japanese_carry_a_utf8_bom() -> None:
    """Windows PowerShell 5.1 reads a BOM-less .ps1 in the ANSI codepage.

    A file holding Japanese then decodes as cp932 and stops parsing - observed
    live as "文字列に終端記号 " がありません" on a script that was valid UTF-8.
    A BOM is what tells 5.1 the file is UTF-8; ``6-verify-ai.ps1`` has carried
    one for the same reason, and checks at runtime that it still has it.
    """
    missing = [
        path.name
        for path in sorted(_repo_root().glob("scripts/*.ps1"))
        if _non_ascii_lines(path) and not path.read_bytes().startswith(b"\xef\xbb\xbf")
    ]
    assert not missing, (
        f"these .ps1 files hold non-ASCII but have no UTF-8 BOM: {missing}. "
        "Windows PowerShell 5.1 will read them as cp932 and fail to parse."
    )
