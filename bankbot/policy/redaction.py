"""Redaction: masks secrets and member PII in anything about to be written down.

Owns: the Redactor. It knows the secret values of this process (credentials
from the environment, parameter values from the run) and the shapes of PII
no run log may contain. It masks a string, the string values inside a
record, and the bytes of a file this codebase did not write.

Does not own: deciding when to redact. evidence/ passes every record through
it at the write boundary; surface/ blurs fields before a screenshot using the
selectors the policy lists. Neither of those decisions lives here.

Governed by ADR-0005 (policy model).
"""

from __future__ import annotations

import html
import json
import os
import re
from collections.abc import Iterable, Sequence
from urllib.parse import quote, quote_plus

MASK = "[REDACTED]"
MASK_BYTES = MASK.encode()

# Anything shorter would mask ordinary words ("a", "id", "the") everywhere.
MIN_SECRET_LENGTH = 4

DEFAULT_SECRET_ENV_SUFFIXES: tuple[str, ...] = ("_KEY", "_TOKEN", "_SECRET", "_ID")

SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# 13 to 19 digits, written as groups of four with an optional space or dash.
CARD_NUMBER_PATTERN = re.compile(r"\b(?:\d{4}[ -]?){3}\d{1,7}\b")
# Account numbers have no fixed shape, so I take any run of 9 to 16 digits.
# This also catches a 16-digit card written without separators.
ACCOUNT_NUMBER_PATTERN = re.compile(r"\b\d{9,16}\b")
# What follows `name=` in a cookie header, up to whatever ends it: the attribute
# separator, the end of a Set-Cookie list, or the quote closing the JSON string a
# trace writes it inside. Keeping those delimiters is what leaves the archive
# parseable and leaves `; HttpOnly; Path=/` readable after the value is gone.
COOKIE_VALUE_BYTES = rb'[^;,"\\\s]*'


class Redactor:
    """Masks known secret values, then anything shaped like an SSN, card or account number.

    Values are matched longest first, so a secret that contains another
    secret is masked as one piece instead of leaving a fragment of the longer
    one readable.
    """

    def __init__(self, secret_values: Iterable[str], cookie_names: Iterable[str] = ()) -> None:
        kept = {value for value in secret_values if len(value) >= MIN_SECRET_LENGTH}
        self._secret_values = sorted(kept, key=len, reverse=True)
        spellings = {written for value in kept for written in _spellings(value)}
        self._secret_spellings = sorted(
            (written.encode() for written in spellings), key=len, reverse=True
        )
        # By name, because the value does not exist yet. A session cookie is minted
        # by the application during the run, and this object is built before the
        # browser opens, so there is no value anyone could have handed it.
        self._cookie_patterns = [
            re.compile(b"(" + re.escape(written.encode()) + b")" + COOKIE_VALUE_BYTES)
            for name in cookie_names
            for written in _spellings(f"{name}=")
        ]

    @classmethod
    def from_environment(
        cls,
        extra_values: Iterable[str] = (),
        suffixes: Sequence[str] | None = None,
        cookie_names: Iterable[str] = (),
    ) -> Redactor:
        """Build a redactor that knows this process's credentials without being told each one.

        Any environment variable whose name ends in one of the suffixes is
        treated as a secret. extra_values is for run parameters: their names
        are logged, their values are not.
        """
        chosen = DEFAULT_SECRET_ENV_SUFFIXES if suffixes is None else tuple(suffixes)
        from_env = [value for name, value in os.environ.items() if name.endswith(chosen)]
        return cls([*from_env, *extra_values], cookie_names=cookie_names)

    def text(self, text: str) -> str:
        """Mask one string; this is the single place the masking rules are applied."""
        for value in self._secret_values:
            text = text.replace(value, MASK)
        text = SSN_PATTERN.sub(MASK, text)
        text = CARD_NUMBER_PATTERN.sub(MASK, text)
        text = ACCOUNT_NUMBER_PATTERN.sub(MASK, text)
        return text

    def record(self, obj: object) -> object:
        """Mask every string inside a log record, however deeply nested, and return a copy.

        Dict keys are left alone: they are field names chosen by the code, and
        masking them would make the log unreadable without hiding anything.
        Scalars other than strings pass through unchanged.
        """
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {key: self.record(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [self.record(item) for item in obj]
        if isinstance(obj, tuple):
            return tuple(self.record(item) for item in obj)
        return obj

    def bytes(self, data: bytes) -> bytes:
        """Mask known values inside a file this codebase did not write, such as a browser trace.

        Known values, and one rule that is not a value: a cookie named in
        `policy.yaml` has whatever follows its `=` masked. None of the shape
        rules `text` applies, because a Playwright trace is full of 13-digit
        millisecond timestamps and the digit-run rule would eat every one of
        them and leave the archive's JSON lines unparseable. The cookie rule
        is safe here where those are not, because it fires only after a name
        somebody wrote down, never on a number that happens to look wrong.
        """
        for spelling in self._secret_spellings:
            data = data.replace(spelling, MASK_BYTES)
        for pattern in self._cookie_patterns:
            # Group 1 is the name and its separator, kept however it was spelled;
            # a percent-encoded `%3D` has no `=` to find by searching for one.
            data = pattern.sub(lambda match: match.group(1) + MASK_BYTES, data)
        return data


def _spellings(value: str) -> set[str]:
    """Raw, JSON-escaped, percent-encoded, form-encoded, HTML-escaped: how a browser writes it."""
    return {
        value,
        json.dumps(value, ensure_ascii=False)[1:-1],
        quote(value, safe=""),
        quote_plus(value),
        html.escape(value),
    }
