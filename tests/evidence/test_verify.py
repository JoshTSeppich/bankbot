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


def write_run(root: Path, run_id: str, redactor: Redactor, step_id: str = "open_start") -> RunDir:
    # A run directory as the system writes it: a log, a result, and the screenshot it names.
    run = RunDir.create(root, run_id)
    writer = EvidenceWriter(run, redactor)
    writer.event(Event.RUN_STARTED, capability="lookup_savings_balance")
    writer.event(Event.STEP_DONE, step_id=step_id)
    run.screenshot_path("final").write_bytes(b"png")
    writer.save_result(
        Success(
            outputs={"savings_balance": "4242.00"},
            evidence=Evidence(run_id=run.run_id, screenshot="screenshots/final.png"),
        )
    )
    writer.event(Event.RUN_FINISHED, kind="success")
    return run


@pytest.fixture
def good_run(tmp_path: Path, redactor: Redactor) -> RunDir:
    return write_run(tmp_path / "evidence", "02-replay-success", redactor)


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


def test_a_repeat_run_whose_event_sequence_differs_from_the_first_is_one_problem(
    good_run: RunDir, redactor: Redactor
) -> None:
    write_run(good_run.path.parent, "02-replay-success-2", redactor, step_id="click_search")
    hashes = sequence_hashes(good_run.path.parent)
    problems = verify_evidence(good_run.path.parent, redactor)
    assert problems == [
        f"02-replay-success-2: event sequence {hashes['02-replay-success-2']} "
        f"differs from 02-replay-success {hashes['02-replay-success']}"
    ]


def test_an_output_extracted_line_that_carries_its_value_is_reported(
    good_run: RunDir, redactor: Redactor
) -> None:
    # What an older build wrote, and what is still sitting in a directory
    # committed before the rule existed. The source walk in test_events.py
    # cannot see it; only the bytes on disk can.
    was = good_run.log_path.read_text(encoding="utf-8").splitlines()
    stale = json.dumps(
        {
            "ts": "2026-09-19T10:00:00+00:00",
            "event": Event.OUTPUT_EXTRACTED.value,
            "output": "savings_balance",
            "value": "4242.00",
        }
    )
    good_run.log_path.write_text("\n".join([*was[:-1], stale, was[-1]]) + "\n", encoding="utf-8")
    problems = verify_evidence(good_run.path.parent, redactor)
    assert problems == [
        "02-replay-success/log.jsonl:3: an output_extracted line carries the value it read"
    ]


def test_an_output_extracted_line_naming_only_the_output_passes(
    good_run: RunDir, redactor: Redactor
) -> None:
    was = good_run.log_path.read_text(encoding="utf-8").splitlines()
    named = json.dumps(
        {
            "ts": "2026-09-19T10:00:00+00:00",
            "event": Event.OUTPUT_EXTRACTED.value,
            "output": "savings_balance",
        }
    )
    good_run.log_path.write_text("\n".join([*was[:-1], named, was[-1]]) + "\n", encoding="utf-8")
    assert verify_evidence(good_run.path.parent, redactor) == []


def test_a_discovery_step_line_that_carries_what_it_extracted_is_reported(
    good_run: RunDir, redactor: Redactor
) -> None:
    # The discovery loop writes its read into the free-text `detail` of a
    # `discovery_step`, so the `output_extracted` rule above never sees it.
    # This is the line run 1 was committed with.
    was = good_run.log_path.read_text(encoding="utf-8").splitlines()
    stale = json.dumps(
        {
            "ts": "2026-09-19T10:00:00+00:00",
            "event": Event.DISCOVERY_STEP.value,
            "index": 3,
            "action": "extract",
            "detail": "extracted savings_balance = 4242.00",
        }
    )
    good_run.log_path.write_text("\n".join([*was[:-1], stale, was[-1]]) + "\n", encoding="utf-8")
    problems = verify_evidence(good_run.path.parent, redactor)
    assert problems == [
        "02-replay-success/log.jsonl:3: a discovery_step line carries what it "
        "extracted, not just which output"
    ]


def test_a_discovery_step_line_naming_only_the_output_it_read_passes(
    good_run: RunDir, redactor: Redactor
) -> None:
    was = good_run.log_path.read_text(encoding="utf-8").splitlines()
    named = json.dumps(
        {
            "ts": "2026-09-19T10:00:00+00:00",
            "event": Event.DISCOVERY_STEP.value,
            "index": 3,
            "action": "extract",
            "detail": "extracted savings_balance",
        }
    )
    good_run.log_path.write_text("\n".join([*was[:-1], named, was[-1]]) + "\n", encoding="utf-8")
    assert verify_evidence(good_run.path.parent, redactor) == []


def test_the_extracted_rule_leaves_the_transcript_alone(
    good_run: RunDir, redactor: Redactor
) -> None:
    # transcript.json is where a read value legitimately lives: the compiler
    # reads it from there. Only log.jsonl is held to the rule.
    good_run.transcript_path.write_text(
        json.dumps({"detail": "extracted savings_balance = 4242.00"}), encoding="utf-8"
    )
    problems = verify_evidence(good_run.path.parent, redactor)
    assert not any("extracted" in problem for problem in problems)
