"""Fault injection: which request a fault lands on, and firing it once.

Owns: the Faults model the admin endpoint accepts, the request counter that
decides when a fault fires, and the once-only bookkeeping. Does not own: what
a fault looks like on the page (templates) or which routes count (app.py
attaches the counter to the /members/... routes only). Governed by ADR-0006:
the faults exist to give replay and the operator handoff something real to
recover from.
"""

import os
import threading
from dataclasses import dataclass

from bankbot.schemas.artifact import StrictModel

# Env var names are the Faults field names with a BANKBOT_ prefix so a fault can be armed before
# the process starts (make targets, CI) without a second vocabulary.
SESSION_EXPIRY_ENV = "BANKBOT_SESSION_EXPIRY_AT_STEP"
UNKNOWN_DIALOG_ENV = "BANKBOT_UNKNOWN_DIALOG_AT_STEP"
SLOW_LOAD_ENV = "BANKBOT_SLOW_LOAD_MS"


class Faults(StrictModel):
    """Which faults are armed and on which counted request each one fires.

    "Counted" means a request to a /members/... route made after the faults
    were set. Login, the admin endpoint and the search-form iframe request do
    not count; they would make the step number depend on how the browser
    fetched the page rather than on what the automation did.
    """

    session_expiry_at_step: int | None = None
    unknown_dialog_at_step: int | None = None
    slow_load_ms: int = 0

    @classmethod
    def from_environment(cls) -> "Faults":
        """Read the startup faults from env vars named like the fields; blank means unset."""
        values: dict[str, str] = {}
        for field, env_name in (
            ("session_expiry_at_step", SESSION_EXPIRY_ENV),
            ("unknown_dialog_at_step", UNKNOWN_DIALOG_ENV),
            ("slow_load_ms", SLOW_LOAD_ENV),
        ):
            raw = os.environ.get(env_name, "").strip()
            if raw:
                values[field] = raw
        return cls.model_validate(values)


@dataclass(frozen=True)
class RequestEffects:
    """What the current counted request must do because of an armed fault."""

    expire_session: bool
    show_dialog: bool


class FaultState:
    """The mutable side of Faults: the counter and whether each one-shot fault has fired.

    A lock guards the counter because uvicorn serves sync routes from a
    thread pool and the browser fetches pages concurrently. Without it two
    requests could both see the Nth count and the fault would fire twice.
    """

    def __init__(self, faults: Faults) -> None:
        self._lock = threading.Lock()
        self.arm(faults)

    def arm(self, faults: Faults) -> None:
        """Replace the armed faults and restart counting, so step numbers are relative to now."""
        with self._lock:
            self.faults = faults
            self.counted = 0
            self._expiry_fired = False
            self._dialog_fired = False

    def count_request(self) -> RequestEffects:
        """Advance the counter for one /members/... request and say which faults fire on it."""
        with self._lock:
            self.counted += 1
            expire_session = False
            show_dialog = False
            if self.faults.session_expiry_at_step == self.counted and not self._expiry_fired:
                self._expiry_fired = True
                expire_session = True
            if self.faults.unknown_dialog_at_step == self.counted and not self._dialog_fired:
                self._dialog_fired = True
                show_dialog = True
            return RequestEffects(expire_session=expire_session, show_dialog=show_dialog)
