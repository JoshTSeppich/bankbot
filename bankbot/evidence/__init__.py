"""Evidence: everything a run leaves on disk, and the one door it leaves through.

Owns: the run directory layout (RunDir), run ids, and the EvidenceWriter that
appends the JSONL log and writes result/transcript/capability JSON. The
writer is the redaction boundary: nothing reaches disk through it without
passing the Redactor first, because these directories are committed to the
repo as evidence.

Does not own: interpreting anything. It does not know what an event means,
what a result says, or when a trace should be kept; callers decide and this
module records. It does not import Playwright: the surface writes
screenshots and trace.zip itself, to paths RunDir hands out.

Governed by ADR-0005 (policy model: the redaction boundary lives here) and
ADR-0003 (error taxonomy: results are persisted as the schema defines them).
"""

from bankbot.evidence.run_dir import RunDir, RunDirectoryExists, RunDirectoryMissing, new_run_id
from bankbot.evidence.writer import EvidenceWriter, Redacting, read_events

__all__ = [
    "EvidenceWriter",
    "Redacting",
    "RunDir",
    "RunDirectoryExists",
    "RunDirectoryMissing",
    "new_run_id",
    "read_events",
]
