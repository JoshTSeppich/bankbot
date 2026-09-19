"""How replay asks for a human, and what it does when there is none.

Owns: the Escalation protocol replay calls when it has run out of rules, and
the Unattended answer used when no operator is attached. A protocol rather
than an exception because an exception unwinds the step loop, and the loop
has to be standing when the human hands control back (ADR-0004).

Does not own: the operator page or the wait for a human (control/).

Governed by ADR-0004 (control transfer).
"""

from typing import Protocol

from bankbot.schemas.intervention import InterventionDecision, InterventionRequest


class Escalation(Protocol):
    """Whoever answers when the engine cannot continue on its own."""

    def request(self, request: InterventionRequest) -> InterventionDecision:
        """Block until someone decides, then say what the engine should do next."""
        ...

    def aborted(self) -> bool:
        """Whether someone has already ended the run; the engine asks before every step."""
        ...


class Unattended:
    """Nobody is there. Every request is answered ABORT, so the run ends as a Failure.

    This is the default so a scheduled replay never hangs waiting for a
    person who will not come.
    """

    def request(self, request: InterventionRequest) -> InterventionDecision:
        """Answer immediately; the Failure carries the request so a human can read it later."""
        return InterventionDecision.ABORT

    def aborted(self) -> bool:
        """Nobody is there to abort."""
        return False
