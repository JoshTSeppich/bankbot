"""Check committed evidence the way a reviewer would, before it is committed.

Owns: verify_evidence, which walks every run directory under a root and
reports what is wrong with it: a log line that does not parse or names an
event the code does not emit, a log that stops before the run finished, a
result or capability or transcript that fails its schema, a screenshot a
file points at that is not there, and any line or trace member that still
carries a secret or something shaped like member PII.

This module imports the policy's redaction rules on purpose. The writer
never does (it is handed a redactor, so evidence/ does not depend on
policy/); the verifier checks the writer's output against the same rules,
so it has to know them. `make verify-evidence` runs it over evidence/.

Governed by ADR-0005 (policy model).
"""

import json
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from bankbot.discover.transcript import Transcript
from bankbot.evidence.events import TIMESTAMP_FIELD, Event, event_sequence_hash
from bankbot.evidence.run_dir import (
    CAPABILITY_FILE,
    LOG_FILE,
    RESULT_FILE,
    TRACE_FILE,
    TRANSCRIPT_FILE,
    RunDir,
)
from bankbot.evidence.trace import IMAGE_SUFFIXES
from bankbot.evidence.writer import read_events
from bankbot.policy.redaction import Redactor
from bankbot.schemas import REPLAY_RESULT_ADAPTER, Capability

# Shapes that mean a review was skipped, whatever the redactor knew at the time.
LEAK_PATTERNS = {
    "api key": re.compile(r"sk-ant-"),
    "home path": re.compile(r"/(Users|home)/[A-Za-z]"),
    "workspace id": re.compile(r"wrkspc_"),
}
SCREENSHOT_FIELD = "screenshot"
# Trace members Playwright writes as one JSON object per line. A line that no
# longer parses is how a redactor that cut too much would show up.
JSON_LINE_MEMBERS = (".trace", ".network")
FINISHED_EVENTS = (Event.RUN_FINISHED, Event.DISCOVERY_FINISHED)


def verify_evidence(root: Path, redactor: Redactor) -> list[str]:
    """Every problem under `root`, one sentence each; an empty list is a pass.

    The redactor is the one this process would write with, so a value in
    the environment right now (the demo credentials, an API key) is caught
    even if it was not a secret when the evidence was written.
    """
    problems: list[str] = []
    run_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    if not run_dirs:
        return [f"{root}: no run directories"]
    for path in run_dirs:
        run = RunDir.open(path)
        problems.extend(_check_log(run))
        problems.extend(_check_json_files(run))
        problems.extend(_check_text_lines(run, redactor))
        problems.extend(_check_trace(run, redactor))
    return problems


def sequence_hashes(root: Path) -> dict[str, str]:
    """The event-sequence hash of every run under `root`, so a reviewer can recompute REPORT.md."""
    return {
        path.name: event_sequence_hash(read_events(RunDir.open(path)))
        for path in sorted(root.iterdir())
        if path.is_dir() and (path / LOG_FILE).exists()
    }


def _check_log(run: RunDir) -> Iterator[str]:
    if not run.log_path.exists():
        yield f"{run.run_id}: no {LOG_FILE}"
        return
    known = {member.value for member in Event}
    last_event: str | None = None
    for number, line in enumerate(run.log_path.read_text(encoding="utf-8").splitlines(), 1):
        where = f"{run.run_id}/{LOG_FILE}:{number}"
        try:
            event = json.loads(line)
        except json.JSONDecodeError as bad:
            yield f"{where}: not JSON ({bad.msg})"
            continue
        if not isinstance(event, dict):
            yield f"{where}: not an object"
            continue
        if event.get("event") not in known:
            yield f"{where}: unknown event {event.get('event')!r}"
        try:
            datetime.fromisoformat(str(event.get(TIMESTAMP_FIELD)))
        except ValueError:
            yield f"{where}: timestamp is not ISO-8601: {event.get(TIMESTAMP_FIELD)!r}"
        yield from _check_screenshot(run, where, event.get(SCREENSHOT_FIELD))
        last_event = str(event.get("event"))
    # A log that stops anywhere else is a run that died with its evidence
    # half written, so what it does say cannot be trusted as the whole story.
    if last_event not in FINISHED_EVENTS:
        yield (
            f"{run.run_id}/{LOG_FILE}: the last event is {last_event!r}, so the run never finished"
        )


def _check_json_files(run: RunDir) -> Iterator[str]:
    if run.result_path.exists():
        try:
            result = REPLAY_RESULT_ADAPTER.validate_json(run.result_path.read_text())
        except ValidationError as bad:
            yield f"{run.run_id}/{RESULT_FILE}: {bad.error_count()} schema errors"
        else:
            yield from _check_screenshot(
                run, f"{run.run_id}/{RESULT_FILE}", result.evidence.screenshot
            )
            intervention = getattr(result, "intervention", None)
            if intervention is not None:
                yield from _check_screenshot(
                    run, f"{run.run_id}/{RESULT_FILE} intervention", intervention.screenshot
                )
    if run.capability_path.exists():
        try:
            Capability.model_validate_json(run.capability_path.read_text())
        except ValidationError as bad:
            yield f"{run.run_id}/{CAPABILITY_FILE}: {bad.error_count()} schema errors"
    if run.transcript_path.exists():
        try:
            Transcript.model_validate_json(run.transcript_path.read_text())
        except ValidationError as bad:
            yield f"{run.run_id}/{TRANSCRIPT_FILE}: {bad.error_count()} schema errors"


def _check_text_lines(run: RunDir, redactor: Redactor) -> Iterator[str]:
    for path in sorted(run.path.glob("*.json*")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            where = f"{run.run_id}/{path.name}:{number}"
            if redactor.text(line) != line:
                yield f"{where}: a secret or PII-shaped value is on this line"
            for label, pattern in LEAK_PATTERNS.items():
                if pattern.search(line):
                    yield f"{where}: looks like {label}"


def _check_trace(run: RunDir, redactor: Redactor) -> Iterator[str]:
    """Playwright writes trace.zip, so it is checked as bytes rather than as lines of JSON."""
    if not run.trace_path.exists():
        return
    with zipfile.ZipFile(run.trace_path) as archive:
        for name in archive.namelist():
            if name.lower().endswith(IMAGE_SUFFIXES):
                continue
            data = archive.read(name)
            where = f"{run.run_id}/{TRACE_FILE}:{name}"
            if redactor.bytes(data) != data:
                yield f"{where}: a secret value is in this member"
            text = data.decode("utf-8", errors="replace")
            for label, pattern in LEAK_PATTERNS.items():
                if pattern.search(text):
                    yield f"{where}: looks like {label}"
            if name.endswith(JSON_LINE_MEMBERS):
                yield from _check_json_lines(where, text)


def _check_json_lines(where: str, text: str) -> Iterator[str]:
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError as bad:
            yield f"{where}:{number}: not JSON ({bad.msg})"


def _check_screenshot(run: RunDir, where: str, reference: object) -> Iterator[str]:
    if isinstance(reference, str) and not (run.path / reference).is_file():
        yield f"{where}: screenshot {reference} is missing"
