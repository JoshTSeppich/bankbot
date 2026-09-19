"""The two tenant variants of the demo app.

Owns: the handful of facts that differ between variant A (/) and variant B
(/b). Does not own: routes or templates; one router factory and one set of
templates take a Variant as input. Governed by ADR-0006.

Variant B exists so the evidence can show a capability recorded on A
replaying on B with a later locator candidate winning (drift), and the
fingerprint check noticing the version change.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Variant:
    """What one tenant's build of the app looks like."""

    prefix: str
    search_button: str
    show_status_column: bool
    application_version: str


VARIANT_A = Variant(
    prefix="",
    search_button="Search",
    show_status_column=False,
    application_version="7.2.1",
)

VARIANT_B = Variant(
    prefix="/b",
    search_button="Find member",
    show_status_column=True,
    application_version="7.3.0",
)
