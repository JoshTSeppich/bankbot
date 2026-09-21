import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from bankbot import cli
from tests.replay.conftest import TEST_SECRETS

FIXTURE = Path(__file__).parent / "fixtures" / "lookup_savings_balance.json"
MISSING_CONTROL = {
    "strategy": "role_name",
    "value": "textbox:Nonexistent",
    "confidence": 0.9,
    "reasoning": "a control that is not there",
}


def _no_dotenv(*args: object, **kwargs: object) -> bool:
    return False


def _cli_with_its_own_browser(argv: list[str]) -> int:
    # Playwright's sync API allows one driver per thread and tests/conftest.py
    # owns the one on this thread, so the CLI gets a thread to open its own in.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(cli.main, argv).result()


@pytest.fixture
def bare_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # main() calls load_dotenv, and the .env on this machine holds a live API
    # key and the demo credentials. Every case below says for itself what the
    # environment has, so none of them can pass or bill by accident.
    monkeypatch.setattr(cli, "load_dotenv", _no_dotenv)
    for name in ("ANTHROPIC_API_KEY", "BANKBOT_USERNAME", "BANKBOT_PASSWORD", "HEADED"):
        monkeypatch.delenv(name, raising=False)


def test_replay_without_a_secret_names_the_variable_and_how_to_set_it(
    bare_environment: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        ["replay", str(FIXTURE), "--param", "member_id=M-100", "--runs-dir", str(tmp_path / "runs")]
    )
    errors = capsys.readouterr().err
    assert code == 2
    assert "BANKBOT_USERNAME" in errors
    assert ".env" in errors


def test_replay_with_a_malformed_input_names_the_pattern_and_the_example(
    bare_environment: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        ["replay", str(FIXTURE), "--param", "member_id=12345", "--runs-dir", str(tmp_path / "runs")]
    )
    errors = capsys.readouterr().err
    assert code == 2
    assert "^M-[0-9]+$" in errors
    assert "M-100" in errors


def test_a_rejected_replay_leaves_no_run_directory_behind(
    bare_environment: None, tmp_path: Path
) -> None:
    runs = tmp_path / "runs"
    code = cli.main(["replay", str(FIXTURE), "--param", "member_id=12345", "--runs-dir", str(runs)])
    assert code == 2
    assert not runs.exists()


# A fresh clone's .env has `ANTHROPIC_API_KEY=` with nothing after it, so blank
# has to count as missing or the run reaches the model and fails there.
@pytest.mark.parametrize("key", [None, ""])
def test_discover_without_a_key_says_replay_does_not_need_one(
    key: str | None,
    bare_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if key is not None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    for name, value in TEST_SECRETS.items():
        monkeypatch.setenv(name, value)
    runs = tmp_path / "runs"
    code = cli.main(["discover", "--param", "member_id=M-100", "--runs-dir", str(runs)])
    errors = capsys.readouterr().err
    assert code == 2
    assert "ANTHROPIC_API_KEY" in errors
    assert "replay" in errors
    assert not runs.exists()


def test_a_caller_error_exits_2_and_a_failure_result_exits_1(
    bare_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
    tmp_path: Path,
    capability_json: dict[str, Any],
) -> None:
    for name, value in TEST_SECRETS.items():
        monkeypatch.setenv(name, value)
    runs = tmp_path / "runs"
    (runs / "taken").mkdir(parents=True)
    malformed = cli.main(["replay", str(FIXTURE), "--param", "member_id", "--runs-dir", str(runs)])
    assert malformed == 2

    taken = cli.main(
        [
            "replay",
            str(FIXTURE),
            "--param",
            "member_id=M-100",
            "--base-url",
            base_url,
            "--runs-dir",
            str(runs),
            "--run-id",
            "taken",
        ]
    )
    assert taken == 2

    capability_json["steps"][1]["target"]["candidates"] = [MISSING_CONTROL]
    broken = tmp_path / "broken-capability.json"
    broken.write_text(json.dumps(capability_json))
    failed = _cli_with_its_own_browser(
        [
            "replay",
            str(broken),
            "--param",
            "member_id=M-100",
            "--base-url",
            base_url,
            "--runs-dir",
            str(runs),
        ]
    )
    assert failed == 1


SHIPPED_RUN = Path(__file__).parent.parent / "evidence" / "01-discovery"


def _saved_run(tmp_path: Path) -> Path:
    """A copy of the committed discovery run with its artifact removed."""
    run = tmp_path / "01-discovery"
    (run / "screenshots").mkdir(parents=True)
    (run / "transcript.json").write_text((SHIPPED_RUN / "transcript.json").read_text())
    return run


def test_compile_re_makes_an_artifact_from_a_saved_run_without_a_key(
    bare_environment: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _saved_run(tmp_path)
    code = cli.main(["compile", str(run)])
    assert code == 0
    written = json.loads((run / "capability.json").read_text())
    assert [step["id"] for step in written["steps"]] == [
        "open_start",
        "type_member_id",
        "click_search",
        "click_link",
        "read_savings_balance",
    ]
    assert str(run / "capability.json") in capsys.readouterr().out


def test_compile_takes_the_transcript_itself_and_writes_the_artifact_beside_it(
    bare_environment: None, tmp_path: Path
) -> None:
    run = _saved_run(tmp_path)
    assert cli.main(["compile", str(run / "transcript.json")]) == 0
    assert (run / "capability.json").exists()


def test_compile_on_a_directory_with_no_transcript_is_a_caller_error(
    bare_environment: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["compile", str(empty)]) == 2
    assert "transcript" in capsys.readouterr().err


def test_compile_of_a_run_that_never_finished_is_a_caller_error(
    bare_environment: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Run 6 is the risky goal the policy blocked, so it stopped without a
    # happy path in it. There is nothing to compile and nothing is written.
    run = tmp_path / "06"
    run.mkdir()
    (run / "transcript.json").write_text(
        (SHIPPED_RUN.parent / "06-discovery-risky-blocked" / "transcript.json").read_text()
    )
    assert cli.main(["compile", str(run)]) == 2
    assert "intervention_aborted" in capsys.readouterr().err
    assert not (run / "capability.json").exists()


def test_replay_times_names_both_runs_and_both_hashes_when_they_disagree(
    bare_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name, value in TEST_SECRETS.items():
        monkeypatch.setenv(name, value)
    # The smallest difference two runs of one capability can have on this app:
    # the second one is sent through the session-expiry recovery, which it
    # survives, so both runs succeed and only the log tells them apart.
    per_run = iter([{}, {"session_expiry_at_step": "3"}])
    arm = cli.arm_faults

    def arm_the_second_run_only(target: str, faults: dict[str, str]) -> None:
        arm(target, next(per_run))

    monkeypatch.setattr(cli, "arm_faults", arm_the_second_run_only)
    code = _cli_with_its_own_browser(
        [
            "replay",
            str(FIXTURE),
            "--param",
            "member_id=M-100",
            "--base-url",
            base_url,
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "pair",
            "--times",
            "2",
        ]
    )
    printed = capsys.readouterr()
    hashes = [
        line.removeprefix("event sequence: ")
        for line in printed.out.splitlines()
        if line.startswith("event sequence: ")
    ]
    assert code == cli.EXIT_RUNS_DISAGREE
    assert len(set(hashes)) == 2, "the two runs were meant to differ"
    assert f"  pair: {hashes[0]}" in printed.err
    assert f"  pair-2: {hashes[1]}" in printed.err
