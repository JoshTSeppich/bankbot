import json
import zipfile
from pathlib import Path

import pytest

from bankbot.evidence import Event, EvidenceWriter, RunDir
from bankbot.evidence.verify import sequence_hashes, verify_evidence
from bankbot.policy import Redactor
from bankbot.schemas import Evidence, Success

SECRET = "hunter2-do-not-log"


@pytest.fixture
def redactor() -> Redactor:
    return Redactor(secret_values=[SECRET])


@pytest.fixture
def good_run(tmp_path: Path, redactor: Redactor) -> RunDir:
    """A run directory as the system writes it: a log, a result, and the screenshot it names."""
    run = RunDir.create(tmp_path / "evidence", "02-replay-success")
    writer = EvidenceWriter(run, redactor)
    writer.event(Event.RUN_STARTED, capability="lookup_savings_balance")
    writer.event(Event.STEP_DONE, step_id="open_start")
    run.screenshot_path("final").write_bytes(b"png")
    writer.save_result(
        Success(
            outputs={"savings_balance": "4242.00"},
            evidence=Evidence(run_id=run.run_id, screenshot="screenshots/final.png"),
        )
    )
    writer.event(Event.RUN_FINISHED, kind="success")
    return run


def test_a_run_directory_as_the_system_writes_it_passes(
    good_run: RunDir, redactor: Redactor
) -> None:
    assert verify_evidence(good_run.path.parent, redactor) == []


def test_an_empty_evidence_root_is_reported_not_passed(tmp_path: Path, redactor: Redactor) -> None:
    assert verify_evidence(tmp_path, redactor) == [f"{tmp_path}: no run directories"]


def test_a_log_line_that_is_not_json_or_names_an_unknown_event_is_reported(
    good_run: RunDir, redactor: Redactor
) -> None:
    with good_run.log_path.open("a") as log:
        log.write("not json\n")
        log.write(json.dumps({"ts": "2026-09-19T10:00:00+00:00", "event": "made_up"}) + "\n")
        log.write(json.dumps({"ts": "yesterday", "event": "step_done"}) + "\n")
    problems = verify_evidence(good_run.path.parent, redactor)
    assert any("log.jsonl:4: not JSON" in p for p in problems)
    assert any("log.jsonl:5: unknown event 'made_up'" in p for p in problems)
    assert any("log.jsonl:6: timestamp is not ISO-8601" in p for p in problems)


def test_a_secret_or_pii_shaped_value_on_any_line_is_reported(
    good_run: RunDir, redactor: Redactor
) -> None:
    leaked = {"ts": "2026-09-19T10:00:00+00:00", "event": "step_done", "typed": SECRET}
    ssn = {"ts": "2026-09-19T10:00:00+00:00", "event": "step_done", "seen": "123-45-6789"}
    key = {"ts": "2026-09-19T10:00:00+00:00", "event": "step_done", "env": "sk-ant-abc"}
    home = {"ts": "2026-09-19T10:00:00+00:00", "event": "step_done", "path": "/Users/someone/x"}
    with good_run.log_path.open("a") as log:
        for line in (leaked, ssn, key, home):
            log.write(json.dumps(line) + "\n")
    problems = verify_evidence(good_run.path.parent, redactor)
    assert any("log.jsonl:4: a secret or PII-shaped value" in p for p in problems)
    assert any("log.jsonl:5: a secret or PII-shaped value" in p for p in problems)
    assert any("log.jsonl:6: looks like api key" in p for p in problems)
    assert any("log.jsonl:7: looks like home path" in p for p in problems)


def test_a_screenshot_a_result_points_at_must_exist(good_run: RunDir, redactor: Redactor) -> None:
    good_run.screenshot_path("final").unlink()
    problems = verify_evidence(good_run.path.parent, redactor)
    assert problems == [
        "02-replay-success/result.json: screenshot screenshots/final.png is missing"
    ]


def test_a_result_that_does_not_match_the_schema_is_reported(
    good_run: RunDir, redactor: Redactor
) -> None:
    good_run.result_path.write_text(json.dumps({"kind": "success", "outputs": "not a dict"}))
    problems = verify_evidence(good_run.path.parent, redactor)
    assert any("result.json" in p and "schema errors" in p for p in problems)


def test_sequence_hashes_cover_every_run_with_a_log(good_run: RunDir) -> None:
    hashes = sequence_hashes(good_run.path.parent)
    assert list(hashes) == ["02-replay-success"]
    assert hashes["02-replay-success"].startswith("sha256:")


def test_verify_evidence_reports_a_secret_inside_a_trace_zip(
    good_run: RunDir, redactor: Redactor
) -> None:
    with zipfile.ZipFile(good_run.trace_path, "w") as archive:
        archive.writestr("trace.trace", json.dumps({"params": {"value": SECRET}}))
    problems = verify_evidence(good_run.path.parent, redactor)
    assert problems == ["02-replay-success/trace.zip:trace.trace: a secret value is in this member"]


def test_verify_evidence_reports_a_broken_json_line_inside_a_trace_zip(
    good_run: RunDir, redactor: Redactor
) -> None:
    with zipfile.ZipFile(good_run.trace_path, "w") as archive:
        archive.writestr("trace.network", '{"type":"resource-snapshot"\n')
    problems = verify_evidence(good_run.path.parent, redactor)
    assert any("trace.zip:trace.network:1: not JSON" in problem for problem in problems)


def test_verify_evidence_reports_a_log_that_never_finished(
    good_run: RunDir, redactor: Redactor
) -> None:
    kept = good_run.log_path.read_text().splitlines()[:-1]
    good_run.log_path.write_text("\n".join(kept) + "\n")
    problems = verify_evidence(good_run.path.parent, redactor)
    assert problems == [
        "02-replay-success/log.jsonl: the last event is 'step_done', so the run never finished"
    ]
