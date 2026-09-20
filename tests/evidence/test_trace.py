import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from bankbot.evidence import EvidenceWriter, RunDir
from bankbot.evidence.trace import redact_trace
from bankbot.policy import MASK, Redactor

# Every spelling of this value differs from every other, which is the point:
# a quote, an ampersand, a space and an at-sign each move under a different rule.
PASSWORD = 'p@ss "w&rd'
SPELLINGS = (
    b'p@ss "w&rd',
    b'p@ss \\"w&rd',
    b"p%40ss%20%22w%26rd",
    b"p%40ss+%22w%26rd",
    b"p@ss &quot;w&amp;rd",
)
POST_BODY = b"username=teller&password=p%40ss+%22w%26rd"
SNAPSHOT = b'<input name="password" value="p@ss &quot;w&amp;rd"><p>p@ss "w&rd</p>'
JPEG = b"\xff\xd8\xff\xe0 a screenshot, never rewritten"
JSON_MEMBERS = ("trace.trace", "trace.network", "trace.stacks")


def build_trace(path: Path, home: Path) -> dict[str, str]:
    """Write a zip shaped like Playwright's, holding the password in all five spellings."""
    post_name = f"resources/{hashlib.sha1(POST_BODY).hexdigest()}.dat"
    snapshot_name = f"resources/{hashlib.sha1(SNAPSHOT).hexdigest()}.html"
    members = {
        "trace.trace": "\n".join(
            [
                json.dumps(
                    {"type": "action", "params": {"value": PASSWORD}, "start": 1789851888077}
                ),
                json.dumps({"type": "log", "message": f'fill("#password", "{PASSWORD}")'}),
            ]
        ).encode(),
        "trace.network": json.dumps(
            {
                "type": "resource-snapshot",
                "snapshot": {
                    "request": {
                        "url": "http://127.0.0.1:8000/login?p=p%40ss%20%22w%26rd",
                        "postData": {"text": POST_BODY.decode()},
                    },
                    "_sha1": post_name.removeprefix("resources/"),
                },
            }
        ).encode(),
        "trace.stacks": json.dumps({"files": [str(home / "code" / "run.py")]}).encode(),
        post_name: POST_BODY,
        snapshot_name: SNAPSHOT,
        "resources/page@abc-1789851888077.jpeg": JPEG,
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 9, 19, 15, 4, 0)), data)
    return {"post": post_name, "snapshot": snapshot_name}


def members_of(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


@pytest.fixture
def run(tmp_path: Path) -> RunDir:
    return RunDir.create(root=tmp_path, run_id="20260919-080000-abcd")


@pytest.fixture
def kept(run: RunDir) -> RunDir:
    """A run whose trace went through EvidenceWriter.keep_trace, the way a real run keeps one."""
    build_trace(run.trace_path, Path.home())
    EvidenceWriter(run, Redactor(secret_values=[PASSWORD])).keep_trace(True)
    return run


def test_a_kept_trace_holds_no_secret_in_any_spelling(kept: RunDir) -> None:
    for name, data in members_of(kept.trace_path).items():
        for spelling in SPELLINGS:
            assert spelling not in data, f"{spelling!r} survived in {name}"


def test_a_kept_trace_holds_no_home_path(kept: RunDir) -> None:
    home = str(Path.home()).encode()
    stacks = members_of(kept.trace_path)["trace.stacks"]
    assert home not in stacks
    assert b"~/code/run.py" in stacks


def test_a_redacted_trace_still_parses_line_by_line(run: RunDir) -> None:
    build_trace(run.trace_path, Path.home())
    redact_trace(run.trace_path, Redactor(secret_values=[PASSWORD]), Path.home())
    members = members_of(run.trace_path)
    for name in JSON_MEMBERS:
        for line in members[name].decode().splitlines():
            json.loads(line)


def test_a_resource_that_held_a_secret_is_renamed_by_its_new_hash(run: RunDir) -> None:
    names = build_trace(run.trace_path, Path.home())
    redact_trace(run.trace_path, Redactor(secret_values=[PASSWORD]), Path.home())
    members = members_of(run.trace_path)

    masked_body = f"username=teller&password={MASK}".encode()
    fresh = f"resources/{hashlib.sha1(masked_body).hexdigest()}.dat"
    assert names["post"] not in members
    assert members[fresh] == masked_body

    network = members["trace.network"].decode()
    assert names["post"].removeprefix("resources/") not in network
    assert fresh.removeprefix("resources/") in network


def test_redacting_a_clean_trace_twice_changes_nothing(run: RunDir) -> None:
    build_trace(run.trace_path, Path.home())
    redactor = Redactor(secret_values=[PASSWORD])
    redact_trace(run.trace_path, redactor, Path.home())
    once = run.trace_path.read_bytes()
    redact_trace(run.trace_path, redactor, Path.home())
    assert run.trace_path.read_bytes() == once
    assert members_of(run.trace_path)["resources/page@abc-1789851888077.jpeg"] == JPEG
