"""Where a run's files live: one directory per run, fixed file names inside it.

Owns: the run id format and the directory layout every other module writes
into. The layout is fixed here once so the operator page, the CLI and the
evidence commits all agree on file names without passing them around.

Does not own: writing any file. EvidenceWriter writes the log and JSON;
the surface writes screenshots and the trace to the paths given here.

Governed by ADR-0005 (policy model).
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

LOG_FILE = "log.jsonl"
RESULT_FILE = "result.json"
TRANSCRIPT_FILE = "transcript.json"
CAPABILITY_FILE = "capability.json"
TRACE_FILE = "trace.zip"
SCREENSHOTS_DIR = "screenshots"
RUN_ID_TIME_FORMAT = "%Y%m%d-%H%M%S"


class RunDirectoryExists(Exception):
    """Refusing to create a run directory over an existing one: evidence is never overwritten."""


class RunDirectoryMissing(Exception):
    """Asked to open a run directory that is not there."""


def new_run_id(at: datetime | None = None) -> str:
    """Name a run so `ls` sorts runs by start time and the name carries no PII.

    UTC timestamp to the second, then four hex characters so two runs in the
    same second do not collide. `at` exists so tests can pin the clock.
    """
    stamp = (at or datetime.now(UTC)).astimezone(UTC).strftime(RUN_ID_TIME_FORMAT)
    return f"{stamp}-{secrets.token_hex(2)}"


@dataclass(frozen=True)
class RunDir:
    """A run's directory. The path is the only state; every file name derives from it.

    Deriving names instead of storing them means there is one place a file
    name can be wrong, and an existing directory can be reopened with no
    metadata beyond its path.
    """

    path: Path

    @classmethod
    def create(cls, root: Path, run_id: str) -> Self:
        """Start a new run under `root`. Raises RunDirectoryExists rather than reuse a directory."""
        path = root / run_id
        if path.exists():
            raise RunDirectoryExists(f"run directory already exists: {path}")
        (path / SCREENSHOTS_DIR).mkdir(parents=True)
        return cls(path=path)

    @classmethod
    def open(cls, path: Path) -> Self:
        """Point at a finished run so the operator page can read it back."""
        if not path.is_dir():
            raise RunDirectoryMissing(f"no run directory at: {path}")
        return cls(path=path)

    @property
    def run_id(self) -> str:
        """The directory name is the run id; nothing else stores it."""
        return self.path.name

    @property
    def log_path(self) -> Path:
        """The append-only JSONL event log."""
        return self.path / LOG_FILE

    @property
    def screenshots_dir(self) -> Path:
        """Where the surface writes screenshots."""
        return self.path / SCREENSHOTS_DIR

    @property
    def trace_path(self) -> Path:
        """Where the surface writes the Playwright trace; deleted on success unless kept."""
        return self.path / TRACE_FILE

    @property
    def result_path(self) -> Path:
        """The ReplayResult as JSON."""
        return self.path / RESULT_FILE

    @property
    def transcript_path(self) -> Path:
        """The discovery Transcript as JSON; absent for replay runs."""
        return self.path / TRANSCRIPT_FILE

    @property
    def capability_path(self) -> Path:
        """The compiled Capability as JSON; absent for replay runs."""
        return self.path / CAPABILITY_FILE

    def screenshot_path(self, name: str) -> Path:
        """Name a screenshot by what it shows (`step_3`) and nothing else.

        The extension and directory are fixed here so the operator page can
        find a screenshot by name without any caller agreeing on a path.
        """
        return self.screenshots_dir / f"{name}.png"
