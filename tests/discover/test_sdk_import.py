import subprocess
import sys

REPLAY_IMPORTS = (
    "import sys, bankbot.cli, bankbot.compile, bankbot.replay, bankbot.evidence.verify; "
    'assert "anthropic" not in sys.modules'
)


def test_replay_and_compile_never_load_the_model_sdk() -> None:
    done = subprocess.run(
        [sys.executable, "-c", REPLAY_IMPORTS], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
