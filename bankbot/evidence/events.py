"""The vocabulary of log events, and the hash that says two runs did the same things.

Owns: Event, the closed list of names a log line's "event" field may carry,
and event_sequence_hash, which reduces a run's log to one string with the
timestamps removed. Two replays of the same capability on the same page
produce the same hash; that is the determinism claim in REPORT.md, checked
rather than asserted.

Does not own: writing events (writer.py) or deciding when to emit one. The
writer takes an Event, not a string, so mypy refuses a name that is not in
this list; a test walks the source to prove no call site slipped past it.

Governed by ADR-0005 (policy model) and ADR-0003 (error taxonomy).
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from enum import StrEnum

TIMESTAMP_FIELD = "ts"


class Event(StrEnum):
    """Every name that can appear in a log line. Adding one here is the whole change."""

    # replay
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    STEP_STARTED = "step_started"
    STEP_DONE = "step_done"
    STEP_FAILED = "step_failed"
    TARGET_RESOLVED = "target_resolved"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_FINISHED = "recovery_finished"
    POLICY_BLOCKED = "policy_blocked"
    OUTPUT_EXTRACTED = "output_extracted"
    RETRY = "retry"
    OUTCOME_MATCHED = "outcome_matched"
    CHECKPOINT_PASSED = "checkpoint_passed"
    WARNING = "warning"
    SCREEN_COMPARED = "screen_compared"
    INTERVENTION_REQUESTED = "intervention_requested"
    INTERVENTION_ANSWERED = "intervention_answered"
    TRACE = "trace"
    # discovery
    DISCOVERY_STARTED = "discovery_started"
    DISCOVERY_STEP = "discovery_step"
    DISCOVERY_FINISHED = "discovery_finished"
    # control
    CONTROL = "control"
    HUMAN_ACTION = "human_action"


def event_sequence_hash(events: Iterable[Mapping[str, object]]) -> str:
    """One sha256 over the run's events in order, timestamps stripped.

    Everything else stays in, including which candidate resolved and what
    was extracted, so two runs hash the same only when they took the same
    path and saw the same things.
    """
    digest = hashlib.sha256()
    for event in events:
        kept = {key: value for key, value in event.items() if key != TIMESTAMP_FIELD}
        digest.update(json.dumps(kept, sort_keys=True, default=str).encode("utf-8"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"
