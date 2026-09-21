"""Compile: turn one discovery Transcript and its goal spec into a Capability.

Owns: the rules of that translation (compile/compiler.py) and the ranking
of an element's facts into locator candidates (compile/candidates.py). It
is deterministic and offline: given the same transcript it produces the
same artifact, and it refuses when the run did not finish, never asserted
a final state, or typed a secret.

Does not own: producing the transcript (discover/) or running the result
(replay/).

Governed by ADR-0001 (artifact schema) and ADR-0002 (locator strategy).
"""

from bankbot.compile.candidates import target_from_facts
from bankbot.compile.compiler import (
    InputNeverUsed,
    NoCheckpointAsserted,
    SecretLeakedIntoTranscript,
    TranscriptNotCompilable,
    compile_capability,
)

__all__ = [
    "InputNeverUsed",
    "NoCheckpointAsserted",
    "SecretLeakedIntoTranscript",
    "TranscriptNotCompilable",
    "compile_capability",
    "target_from_facts",
]
