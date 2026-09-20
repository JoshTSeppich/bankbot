import copy

import pytest

from bankbot.policy import MASK, Redactor

FAKE_KEY = "fake-key-value-for-tests-1234"


def test_env_var_ending_in_key_has_its_value_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SERVICE_KEY", FAKE_KEY)
    redactor = Redactor.from_environment()
    assert redactor.text(f"authorization: {FAKE_KEY}") == f"authorization: {MASK}"


def test_env_var_not_matching_a_suffix_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SERVICE_COLOUR", "cornflower-blue-7781")
    redactor = Redactor.from_environment()
    assert redactor.text("theme cornflower-blue-7781") == "theme cornflower-blue-7781"


def test_short_values_are_never_treated_as_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SERVICE_KEY", "ab1")
    redactor = Redactor.from_environment(extra_values=["M-1"])
    assert redactor.text("ab1 and M-1 stay readable") == "ab1 and M-1 stay readable"


def test_param_value_is_masked_inside_a_longer_string() -> None:
    redactor = Redactor(secret_values=["M-100"])
    assert redactor.text("No member found for M-100.") == f"No member found for {MASK}."


def test_longer_secret_containing_a_shorter_one_is_masked_whole() -> None:
    redactor = Redactor(secret_values=["M-100", "M-1001"])
    assert redactor.text("id M-1001") == f"id {MASK}"


def test_ssn_shaped_number_is_masked() -> None:
    assert Redactor([]).text("ssn 000-00-0000 on file") == f"ssn {MASK} on file"


def test_card_shaped_number_is_masked() -> None:
    redactor = Redactor([])
    assert redactor.text("card 0000 0000 0000 0000") == f"card {MASK}"
    assert redactor.text("card 0000-0000-0000-0000") == f"card {MASK}"


def test_nine_to_sixteen_digit_run_is_masked() -> None:
    redactor = Redactor([])
    assert redactor.text("acct 000000000") == f"acct {MASK}"
    assert redactor.text("acct 0000000000000000") == f"acct {MASK}"


def test_eight_digit_run_is_left_alone_so_ordinary_numbers_survive() -> None:
    assert Redactor([]).text("ref 00000000, balance $4,242.00") == "ref 00000000, balance $4,242.00"


def test_record_redaction_reaches_nested_lists_and_dicts_and_leaves_keys() -> None:
    redactor = Redactor(secret_values=["hunter2-demo"])
    record = {
        "hunter2-demo": "a key that looks like a secret is left alone",
        "params": {"password": "hunter2-demo", "member_id": "M-100"},
        "steps": [{"typed": "hunter2-demo"}, ("tuple", "hunter2-demo")],
        "count": 3,
        "ok": True,
        "nothing": None,
    }
    redacted = redactor.record(record)
    assert redacted == {
        "hunter2-demo": "a key that looks like a secret is left alone",
        "params": {"password": MASK, "member_id": "M-100"},
        "steps": [{"typed": MASK}, ("tuple", MASK)],
        "count": 3,
        "ok": True,
        "nothing": None,
    }


def test_record_redaction_does_not_mutate_the_input() -> None:
    redactor = Redactor(secret_values=["hunter2-demo"])
    record = {"params": {"password": "hunter2-demo"}, "steps": ["hunter2-demo"]}
    before = copy.deepcopy(record)
    redactor.record(record)
    assert record == before


def test_bytes_masks_a_known_value_in_every_spelling_a_browser_writes() -> None:
    redactor = Redactor(secret_values=['p@ss "w&rd'])
    written = (
        b'raw=p@ss "w&rd json=p@ss \\"w&rd percent=p%40ss%20%22w%26rd'
        b" form=p%40ss+%22w%26rd html=p@ss &quot;w&amp;rd"
    )
    assert redactor.bytes(written) == (
        f"raw={MASK} json={MASK} percent={MASK} form={MASK} html={MASK}".encode()
    )


def test_bytes_leaves_a_millisecond_timestamp_alone_where_text_would_mask_it() -> None:
    redactor = Redactor([])
    assert redactor.bytes(b'{"startTime":1789851888077}') == b'{"startTime":1789851888077}'
    assert redactor.text('{"startTime":1789851888077}') != '{"startTime":1789851888077}'
