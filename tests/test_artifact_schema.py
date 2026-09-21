import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from bankbot.schemas import Capability, SecretRef, StateAssertion, TargetRef

FIXTURE = Path(__file__).parent / "fixtures" / "lookup_savings_balance.json"


def load_example() -> dict[str, Any]:
    example: dict[str, Any] = json.loads(FIXTURE.read_text())
    return example


def test_example_artifact_round_trips_through_json_unchanged() -> None:
    original = Capability.model_validate_json(FIXTURE.read_text())
    reloaded = Capability.model_validate_json(original.model_dump_json())
    assert reloaded == original


def test_example_artifact_is_written_in_canonical_form_so_it_doubles_as_reference() -> None:
    original = Capability.model_validate_json(FIXTURE.read_text())
    assert original.model_dump(mode="json") == load_example()


def test_unknown_field_is_rejected_so_typos_in_hand_written_artifacts_fail_loudly() -> None:
    example = load_example()
    example["recoverys"] = []
    with pytest.raises(ValidationError, match="recoverys"):
        Capability.model_validate(example)


def test_target_ref_requires_at_least_one_candidate() -> None:
    with pytest.raises(ValidationError):
        TargetRef(candidates=[])


def test_state_assertion_with_nothing_to_check_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one condition"):
        StateAssertion(description="vacuous")


def test_step_referencing_undeclared_input_is_rejected_at_load_time() -> None:
    example = load_example()
    del example["inputs"]["member_id"]
    with pytest.raises(ValidationError, match="undeclared input 'member_id'"):
        Capability.model_validate(example)


def test_a_capability_whose_input_no_step_uses_is_rejected() -> None:
    example = load_example()
    example["inputs"]["branch"] = {"type": "string", "required": True, "example": "Riverside"}
    with pytest.raises(ValidationError, match="input 'branch' is never used"):
        Capability.model_validate(example)


def test_a_locator_candidate_may_name_a_declared_input() -> None:
    example = load_example()
    example["steps"][3]["target"]["candidates"] = [
        {
            "strategy": "role_name",
            "value": "link:{input:member_id}",
            "confidence": 0.95,
            "reasoning": "The caller asked for this member by id.",
        }
    ]
    capability = Capability.model_validate(example)
    assert capability.steps[3].target is not None
    assert capability.steps[3].target.candidates[0].value == "link:{input:member_id}"


def test_a_locator_candidate_naming_an_undeclared_input_is_rejected() -> None:
    example = load_example()
    example["steps"][3]["target"]["candidates"][0]["value"] = "link:{input:branch}"
    with pytest.raises(ValidationError, match="undeclared input 'branch'"):
        Capability.model_validate(example)


def test_an_input_a_locator_names_counts_as_used_even_when_no_step_types_it() -> None:
    example = load_example()
    example["steps"][1]["value"] = {"literal": "M-100"}
    example["steps"][3]["target"]["candidates"][0]["value"] = "link:{input:member_id}"
    assert Capability.model_validate(example).inputs["member_id"].required


def test_step_referencing_undeclared_recovery_is_rejected_at_load_time() -> None:
    example = load_example()
    example["steps"][0]["on_fail"] = {"kind": "recover", "recovery_id": "session_expired"}
    example["recoveries"] = []
    with pytest.raises(ValidationError, match="undeclared recovery 'session_expired'"):
        Capability.model_validate(example)


def test_extract_step_must_name_a_declared_output() -> None:
    example = load_example()
    del example["outputs"]["savings_balance"]
    with pytest.raises(ValidationError, match="undeclared output 'savings_balance'"):
        Capability.model_validate(example)


def test_duplicate_step_ids_across_steps_and_recoveries_are_rejected() -> None:
    example = load_example()
    example["recoveries"][0]["steps"][0]["id"] = "open_search"
    with pytest.raises(ValidationError, match="unique"):
        Capability.model_validate(example)


def test_version_must_be_semver() -> None:
    example = load_example()
    example["version"] = "v1"
    with pytest.raises(ValidationError, match="version"):
        Capability.model_validate(example)


def test_secret_ref_names_an_environment_variable_and_is_never_a_caller_input() -> None:
    assert SecretRef(secret="BANKBOT_PASSWORD").secret == "BANKBOT_PASSWORD"
    with pytest.raises(ValidationError):
        SecretRef(secret="password")


def test_target_ref_defaults_to_the_top_document_when_no_frame_path_is_given() -> None:
    capability = Capability.model_validate(load_example())
    assert capability.outputs["savings_balance"].extract.frame_path == []
    assert capability.steps[1].target is not None
    assert capability.steps[1].target.frame_path == ["main"]


def test_created_from_run_carries_model_and_request_ids_for_audit() -> None:
    capability = Capability.model_validate(load_example())
    assert capability.created_from_run is not None
    assert capability.created_from_run.model == "none"
    assert capability.created_from_run.request_ids == []
