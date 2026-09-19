import json

from bankbot.schemas import REPLAY_RESULT_ADAPTER, Evidence, Failure, Outcome, ReplayResult, Success

EVIDENCE = Evidence(run_id="run-0002", screenshot="runs/run-0002/final.png")


def test_each_replay_result_variant_round_trips_through_json_by_its_kind() -> None:
    results: list[ReplayResult] = [
        Success(outputs={"savings_balance": "1234.56"}, evidence=EVIDENCE),
        Outcome(code="member_not_found", message="No member exists.", evidence=EVIDENCE),
        Failure(step_id="open_member", expected="a link", observed="modal", evidence=EVIDENCE),
    ]
    for result in results:
        reloaded = REPLAY_RESULT_ADAPTER.validate_json(REPLAY_RESULT_ADAPTER.dump_json(result))
        assert reloaded == result


def test_known_outcome_is_reported_as_outcome_not_failure() -> None:
    raw = json.dumps(
        {
            "kind": "outcome",
            "code": "member_not_found",
            "message": "No member exists.",
            "evidence": {"run_id": "run-0003"},
        }
    )
    result = REPLAY_RESULT_ADAPTER.validate_json(raw)
    assert isinstance(result, Outcome)
