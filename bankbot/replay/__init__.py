"""Replay: run a Capability against a live app with no model in the loop.

Owns: the executor that walks the steps, resolves each target through the
surface, and the classifier that turns whatever happened next into one of
three results: Success, a known Outcome, or a Failure. The classification
order is fixed here (known outcome, known recovery, the step's own on_fail,
then a human) because that order is the error taxonomy.

Does not own: how the artifact was made (discover/, compile/), how a page is
perceived or driven (surface/), what happens while a human is in control
(control/), or what is allowed (policy/; replay only asks).

Governed by ADR-0003 (error taxonomy) and ADR-0002 (locator strategy: the
winning candidate index is the drift signal).
"""

from bankbot.replay.escalation import Escalation, Unattended
from bankbot.replay.run import Replay
from bankbot.replay.steps import StepFailed, StepRunner
from bankbot.replay.values import ParamInvalid, ParamMissing, SecretMissing

__all__ = [
    "Escalation",
    "ParamInvalid",
    "ParamMissing",
    "Replay",
    "SecretMissing",
    "StepFailed",
    "StepRunner",
    "Unattended",
]
