"""The writer every run output goes through, and the redaction that happens on the way.

Owns: appending JSONL events, writing result/transcript/capability JSON, and
the trace keep-or-delete decision's execution. Every byte that reaches disk
here is redacted: a kept trace through trace.py, everything else twice.
First `redactor.record` walks the structure and masks string values. Then,
after JSON serialisation, `redactor.text` masks the finished line. Two
passes because they catch different things: the structural pass sees values
before they are quoted and escaped, so it masks reliably; the text pass
catches values that were not strings when the structural pass ran (Paths,
enums, exceptions) and only became text during serialisation. Neither pass
alone covers both.

Does not own: what to redact (bankbot.policy.Redactor) or what any event
means. The writer never inspects an event name.

Governed by ADR-0005 (policy model) and ADR-0003 (error taxonomy).
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from bankbot.evidence.events import TIMESTAMP_FIELD, Event
from bankbot.evidence.run_dir import RunDir
from bankbot.evidence.trace import redact_trace
from bankbot.schemas.result import REPLAY_RESULT_ADAPTER, ReplayResult


class Redacting(Protocol):
    """What the writer needs from a redactor. bankbot.policy.Redactor satisfies this.

    A Protocol rather than an import so evidence/ does not depend on policy/;
    tests prove the boundary with a two-line fake.
    """

    def text(self, s: str) -> str:
        """Mask secrets inside one string."""
        ...

    def record(self, obj: object) -> object:
        """Mask secrets in every string value inside a nested dict/list structure."""
        ...

    def bytes(self, data: bytes) -> bytes:
        """Mask secrets in the bytes of a file this codebase did not write."""
        ...


class EvidenceWriter:
    """Appends to one run's log and writes its JSON files, redacting everything on the way."""

    def __init__(self, run_dir: RunDir, redactor: Redacting) -> None:
        self._run = run_dir
        self._redactor = redactor

    def event(self, event: Event, **fields: object) -> None:
        """Append one JSON line. The file is opened and closed per event.

        Per-event open costs a syscall and buys two things: a crash
        mid-run loses nothing already logged, and there is no handle for a
        caller to forget to close. The timestamp is ISO-8601 with
        separators, which is also why the digit-run redaction leaves it
        alone: a compact YYYYMMDDHHMMSS would look like an account number.
        """
        record = {TIMESTAMP_FIELD: datetime.now(UTC).isoformat(), "event": event.value, **fields}
        line = self._render(record, indent=None)
        with self._run.log_path.open("a", encoding="utf-8") as log:
            log.write(line + "\n")
            log.flush()

    def save_result(self, result: ReplayResult) -> None:
        """Persist the ReplayResult as result.json, via the adapter because the type is a union."""
        payload = REPLAY_RESULT_ADAPTER.dump_python(result, mode="json")
        self._write_json(self._run.result_path, payload)

    def save_model(self, name: str, model: BaseModel) -> None:
        """Persist any boundary model as <name>.json; transcript and capability use this."""
        self._write_json(self._run.path / f"{name}.json", model.model_dump(mode="json"))

    def keep_trace(self, keep: bool) -> None:
        """Delete trace.zip unless asked to keep it, redact it if it stays, log either way.

        A kept trace is redacted here rather than by its writer because
        Playwright writes it, not this codebase, and a run directory may not
        hold a file that never passed the redactor. A run that lost its
        session writes no trace at all, so there is nothing to redact then.
        Logged even when nothing is deleted so a missing trace in the evidence
        directory is explained by the log, not left to guesswork.
        """
        existed = self._run.trace_path.exists()
        if keep and existed:
            redact_trace(self._run.trace_path, self._redactor, Path.home())
        if not keep and existed:
            self._run.trace_path.unlink()
        self.event(Event.TRACE, file=self._run.trace_path.name, kept=keep, existed=existed)

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(self._render(payload, indent=2) + "\n", encoding="utf-8")

    def _render(self, payload: object, indent: int | None) -> str:
        redacted = self._redactor.record(payload)
        # default=str turns Paths, datetimes and enums into text; ensure_ascii=False
        # keeps non-ASCII secrets recognisable to the text pass instead of \u-escaped.
        line = json.dumps(redacted, indent=indent, default=str, ensure_ascii=False)
        return self._redactor.text(line)


def read_events(run_dir: RunDir) -> list[dict[str, object]]:
    """Load a run's log back in order; a run that has not logged yet reads as empty."""
    if not run_dir.log_path.exists():
        return []
    events: list[dict[str, object]] = []
    with run_dir.log_path.open(encoding="utf-8") as log:
        for line in log:
            if line.strip():
                events.append(json.loads(line))
    return events
