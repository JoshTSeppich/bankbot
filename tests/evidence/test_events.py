import ast
from datetime import datetime
from pathlib import Path

from bankbot.evidence import Event, EvidenceWriter, RunDir, event_sequence_hash, read_events
from bankbot.policy import Redactor

PACKAGE = Path(__file__).parent.parent.parent / "bankbot"


def event_call_first_args() -> list[tuple[str, int, ast.expr]]:
    """Every `<something>.event(...)` call in the package with its first argument."""
    found: list[tuple[str, int, ast.expr]] = []
    for source in PACKAGE.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "event"
                and node.args
            ):
                found.append((str(source.relative_to(PACKAGE)), node.lineno, node.args[0]))
    return found


def test_every_writer_event_call_site_names_a_member_of_the_event_enum() -> None:
    calls = event_call_first_args()
    assert len(calls) > 20, "the walk found fewer call sites than the package has"
    offenders = []
    for path, line, first in calls:
        is_member = (
            isinstance(first, ast.Attribute)
            and isinstance(first.value, ast.Name)
            and first.value.id == "Event"
            and first.attr in Event.__members__
        )
        if not is_member:
            offenders.append(f"{path}:{line}")
    assert offenders == []


def test_a_log_lines_timestamp_is_iso_8601_and_survives_redaction(tmp_path: Path) -> None:
    run = RunDir.create(tmp_path, "20260919-080000-abcd")
    # The account-number rule masks any 9 to 16 digit run; a timestamp written
    # without separators would be eaten by it. This proves ours is not.
    writer = EvidenceWriter(run, Redactor(secret_values=["hunter2-do-not-log"]))
    writer.event(Event.STEP_DONE, step_id="s1", account="123456789012")

    event = read_events(run)[0]
    assert event["account"] == "[REDACTED]", "the digit rule is live on this writer"
    stamp = str(event["ts"])
    assert "[REDACTED]" not in stamp
    assert datetime.fromisoformat(stamp).tzinfo is not None
    assert "T" in stamp and "-" in stamp and ":" in stamp


def test_the_same_events_hash_the_same_regardless_of_when_they_happened() -> None:
    first = [
        {"ts": "2026-09-19T10:00:00+00:00", "event": "step_started", "step_id": "a"},
        {"ts": "2026-09-19T10:00:01+00:00", "event": "step_done", "step_id": "a"},
    ]
    later = [
        {"ts": "2026-09-20T22:15:09+00:00", "event": "step_started", "step_id": "a"},
        {"ts": "2026-09-20T22:15:11+00:00", "event": "step_done", "step_id": "a"},
    ]
    assert event_sequence_hash(first) == event_sequence_hash(later)
    assert event_sequence_hash(first).startswith("sha256:")


def test_a_different_path_through_the_run_hashes_differently() -> None:
    base: list[dict[str, object]] = [
        {"ts": "t", "event": "step_started", "step_id": "a"},
        {"ts": "t", "event": "target_resolved", "step_id": "a", "candidate_index": 0},
    ]
    drifted: list[dict[str, object]] = [
        {"ts": "t", "event": "step_started", "step_id": "a"},
        {"ts": "t", "event": "target_resolved", "step_id": "a", "candidate_index": 1},
    ]
    reordered = list(reversed(base))
    assert event_sequence_hash(base) != event_sequence_hash(drifted)
    assert event_sequence_hash(base) != event_sequence_hash(reordered)
