"""Redaction: masks secrets and member PII in anything about to be written down.

Owns: the Redactor. It knows the secret values of this process (credentials
from the environment, parameter values from the run) and the shapes of PII
no run log may contain. It masks a string, and the string values inside a
record.

Does not own: deciding when to redact. evidence/ passes every record through
it at the write boundary; surface/ blurs fields before a screenshot using the
selectors the policy lists. Neither of those decisions lives here.

Governed by ADR-0005 (policy model).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence

MASK = "[REDACTED]"

# Anything shorter would mask ordinary words ("a", "id", "the") everywhere.
MIN_SECRET_LENGTH = 4

DEFAULT_SECRET_ENV_SUFFIXES: tuple[str, ...] = ("_KEY", "_TOKEN", "_SECRET", "_ID")

SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# 13 to 19 digits, written as groups of four with an optional space or dash.
CARD_NUMBER_PATTERN = re.compile(r"\b(?:\d{4}[ -]?){3}\d{1,7}\b")
# Account numbers have no fixed shape, so I take any run of 9 to 16 digits.
# This also catches a 16-digit card written without separators.
ACCOUNT_NUMBER_PATTERN = re.compile(r"\b\d{9,16}\b")


class Redactor:
    """Masks known secret values, then anything shaped like an SSN, card or account number.

    Values are matched longest first, so a secret that contains another
    secret is masked as one piece instead of leaving a fragment of the longer
    one readable.
    """

    def __init__(self, secret_values: Iterable[str]) -> None:
        kept = {value for value in secret_values if len(value) >= MIN_SECRET_LENGTH}
        self._secret_values = sorted(kept, key=len, reverse=True)

    @classmethod
    def from_environment(
        cls,
        extra_values: Iterable[str] = (),
        suffixes: Sequence[str] | None = None,
    ) -> Redactor:
        """Build a redactor that knows this process's credentials without being told each one.

        Any environment variable whose name ends in one of the suffixes is
        treated as a secret. extra_values is for run parameters: their names
        are logged, their values are not.
        """
        chosen = DEFAULT_SECRET_ENV_SUFFIXES if suffixes is None else tuple(suffixes)
        from_env = [value for name, value in os.environ.items() if name.endswith(chosen)]
        return cls([*from_env, *extra_values])

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
