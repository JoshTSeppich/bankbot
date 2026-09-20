import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bankbot.evidence import Event, EvidenceWriter, Redacting, RunDir, read_events
from bankbot.schemas import Evidence, Failure
from bankbot.schemas.artifact import StrictModel

SECRET = "hunter2-do-not-log"


class FakeRedactor:
    """Replaces one known string. Enough to prove the boundary without depending on policy/."""

    def text(self, s: str) -> str:
        return s.replace(SECRET, "[REDACTED]")

    def record(self, obj: object) -> object:
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {key: self.record(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [self.record(item) for item in obj]
        return obj

    def bytes(self, data: bytes) -> bytes:
        return data.replace(SECRET.encode(), b"[REDACTED]")


def write_trace(path: Path) -> None:
    """A trace.zip the writer can open: keep_trace(True) now rewrites the archive."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("trace.trace", json.dumps({"type": "context-options"}))
        archive.writestr("resources/page@abc-1.jpeg", b"\xff\xd8\xff\xe0")


class Note(StrictModel):
    body: str


@pytest.fixture
def run(tmp_path: Path) -> RunDir:
    return RunDir.create(root=tmp_path, run_id="20260919-080000-abcd")


@pytest.fixture
def writer(run: RunDir) -> EvidenceWriter:
    redactor: Redacting = FakeRedactor()
    return EvidenceWriter(run, redactor)


def secret_on_disk(run: RunDir) -> bool:
    files = [p for p in run.path.rglob("*") if p.is_file()]
    assert files, "nothing was written, so the test proves nothing"
    return any(SECRET.encode() in p.read_bytes() for p in files)


def test_a_secret_passed_in_an_event_field_never_reaches_disk(
    run: RunDir, writer: EvidenceWriter
) -> None:
    writer.event(Event.STEP_STARTED, value=SECRET)
    assert not secret_on_disk(run)
    assert read_events(run)[0]["value"] == "[REDACTED]"


def test_a_secret_nested_inside_a_list_inside_a_dict_never_reaches_disk(
    run: RunDir, writer: EvidenceWriter
) -> None:
    writer.event(Event.DISCOVERY_STEP, detail={"frames": [{"aria": f"textbox: {SECRET}"}]})
    assert not secret_on_disk(run)


def test_a_secret_inside_a_non_string_field_is_caught_by_the_text_pass(
    run: RunDir, writer: EvidenceWriter
) -> None:
    # A Path is not a str, so the structural pass leaves it alone; it only
    # becomes text during serialisation. The second pass is for this case.
    writer.event(Event.STEP_DONE, path=Path("screenshots") / f"{SECRET}.png")
    assert not secret_on_disk(run)


def test_a_secret_inside_a_saved_result_never_reaches_disk(
    run: RunDir, writer: EvidenceWriter
) -> None:
    result = Failure(
        step_id="open_member",
        expected="a link",
        observed=f"page said {SECRET}",
        evidence=Evidence(run_id=run.run_id),
    )
    writer.save_result(result)
    assert run.result_path.exists()
    assert not secret_on_disk(run)
    assert json.loads(run.result_path.read_text())["kind"] == "failure"


def test_a_secret_inside_a_saved_model_never_reaches_disk(
    run: RunDir, writer: EvidenceWriter
) -> None:
    writer.save_model("transcript", Note(body=f"typed {SECRET} into Password"))
    assert run.transcript_path.exists()
    assert not secret_on_disk(run)


def test_each_event_is_one_json_line_with_timestamp_and_event_name(
    run: RunDir, writer: EvidenceWriter
) -> None:
    writer.event(Event.STEP_STARTED, step_id="s1")
    writer.event(Event.STEP_DONE, step_id="s1", detail={"multi": "line\nvalue"})

    lines = run.log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "step_started"
    assert first["step_id"] == "s1"
    assert datetime.fromisoformat(first["ts"]).tzinfo == UTC


def test_events_are_flushed_immediately_so_a_crash_keeps_the_log(
    run: RunDir, writer: EvidenceWriter
) -> None:
    writer.event(Event.STEP_STARTED, step_id="s1")
    # No close, no flush, no del: the line must already be on disk.
    assert run.log_path.read_text(encoding="utf-8").count("\n") == 1


def test_keep_trace_false_deletes_the_trace_and_records_it(
    run: RunDir, writer: EvidenceWriter
) -> None:
    write_trace(run.trace_path)
    writer.keep_trace(False)
    assert not run.trace_path.exists()
    last = read_events(run)[-1]
    assert last["event"] == "trace"
    assert last["kept"] is False
    assert last["existed"] is True


def test_keep_trace_true_leaves_the_trace_and_records_it(
    run: RunDir, writer: EvidenceWriter
) -> None:
    write_trace(run.trace_path)
    writer.keep_trace(True)
    assert run.trace_path.exists()
    assert read_events(run)[-1]["kept"] is True


def test_keep_trace_true_with_no_trace_on_disk_is_recorded_and_not_an_error(
    run: RunDir, writer: EvidenceWriter
) -> None:
    writer.keep_trace(True)
    assert not run.trace_path.exists()
    assert read_events(run)[-1]["existed"] is False


def test_read_events_returns_what_was_written_in_order(run: RunDir, writer: EvidenceWriter) -> None:
    for index in range(5):
        writer.event(Event.STEP_DONE, n=index)
    assert [event["n"] for event in read_events(run)] == [0, 1, 2, 3, 4]


def test_read_events_on_a_run_that_has_not_logged_is_empty(run: RunDir) -> None:
    assert read_events(run) == []
