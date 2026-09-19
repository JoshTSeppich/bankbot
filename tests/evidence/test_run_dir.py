import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bankbot.evidence import RunDir, RunDirectoryExists, RunDirectoryMissing, new_run_id

RUN_ID_SHAPE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


def test_run_ids_sort_chronologically() -> None:
    earlier = new_run_id(datetime(2026, 9, 19, 8, 0, 0, tzinfo=UTC))
    later = new_run_id(datetime(2026, 9, 19, 8, 0, 1, tzinfo=UTC))
    assert earlier < later
    assert RUN_ID_SHAPE.match(new_run_id())


def test_run_dir_layout_matches_the_documented_shape(tmp_path: Path) -> None:
    run = RunDir.create(root=tmp_path, run_id="20260919-080000-abcd")

    assert run.path == tmp_path / "20260919-080000-abcd"
    assert run.run_id == "20260919-080000-abcd"
    assert run.screenshots_dir == run.path / "screenshots"
    assert run.screenshots_dir.is_dir()
    assert run.log_path == run.path / "log.jsonl"
    assert run.trace_path == run.path / "trace.zip"
    assert run.result_path == run.path / "result.json"
    assert run.transcript_path == run.path / "transcript.json"
    assert run.capability_path == run.path / "capability.json"
    assert run.screenshot_path("step_3") == run.screenshots_dir / "step_3.png"


def test_creating_a_run_dir_twice_refuses_to_overwrite_evidence(tmp_path: Path) -> None:
    RunDir.create(root=tmp_path, run_id="r1")
    with pytest.raises(RunDirectoryExists):
        RunDir.create(root=tmp_path, run_id="r1")


def test_opening_an_existing_run_dir_gives_the_same_paths(tmp_path: Path) -> None:
    created = RunDir.create(root=tmp_path, run_id="r1")
    opened = RunDir.open(created.path)
    assert opened == created


def test_opening_a_missing_run_dir_is_a_named_error(tmp_path: Path) -> None:
    with pytest.raises(RunDirectoryMissing):
        RunDir.open(tmp_path / "nope")
