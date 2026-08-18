r"""Make output survive a console that cannot encode every character.

A Japanese Windows console runs on cp932, which has no code point for U+00A5
(``¥``). Python's stdout falls back to an escape, so a summary that read
"revenue rose to ¥124.0 billion" arrives as ``\xa5124.0 billion`` - observed
live, in the money figure, in a tool whose entire subject is money.

Three answers were possible and two are wrong. Forcing UTF-8 breaks the
Japanese that currently renders correctly, because the console is genuinely
cp932 and ``ホクト`` is a cp932 character. Dropping the offender to ``?`` loses
the currency. The third is that cp932 *does* carry a yen sign - the fullwidth
``￥`` (U+FFE5) - so the character has an equivalent the console can print, and
the only thing missing is the mapping.

Registered as a codec error handler rather than applied at print sites: model
output reaches the screen through summaries, alerts, notifications and log
records alike, and a substitution that covers only the ones remembered today
is the same bug waiting for the next call site.
"""

from __future__ import annotations

import codecs
import contextlib
import sys
from typing import IO, Any

#: Characters with an equivalent that legacy consoles can encode. Chosen so the
#: meaning survives: a currency stays a currency, punctuation stays punctuation.
SUBSTITUTIONS: dict[str, str] = {
    "¥": "￥",  # ¥ -> ￥ (cp932 carries the fullwidth form only)
    "—": "-",  # em dash
    "–": "-",  # en dash
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    " ": " ",  # non-breaking space
}

ERROR_HANDLER = "stock_ai_console_safe"


def _substitute(error: UnicodeError) -> tuple[str, int]:
    """Replace an unencodable run with equivalents, one character at a time."""
    if not isinstance(error, UnicodeEncodeError):  # pragma: no cover - decode side
        raise error
    text = error.object[error.start : error.end]
    return "".join(SUBSTITUTIONS.get(char, "?") for char in text), error.end


codecs.register_error(ERROR_HANDLER, _substitute)


def _reconfigure(stream: IO[Any] | None) -> None:
    """Point one stream at the substituting handler, if it can be pointed."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:  # a pipe wrapper, a test capture, a closed stream
        return
    # A detached or already-closed stream is not a reason to fail a command.
    with contextlib.suppress(ValueError, OSError):
        reconfigure(errors=ERROR_HANDLER)


def install() -> None:
    """Make stdout and stderr substitute rather than escape.

    Safe to call more than once, and a no-op where the encoding already covers
    everything - on a UTF-8 terminal the handler is never reached.
    """
    _reconfigure(sys.stdout)
    _reconfigure(sys.stderr)
