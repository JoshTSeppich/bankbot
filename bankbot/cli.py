"""The command line: discover, compile, replay, operator, verify-evidence. Wiring, and a judgement.

Owns: argument parsing, the pre-flight that decides whether a run may start,
starting the demo app in-process when no base URL is given, opening the
browser, starting the operator pages when a person can see the browser
(HEADED=1), and printing the result.

The one thing this file decides is whose fault a stop is. A condition of the
world the caller can fix is one line on stderr and exit 2; a run that started
and ended badly is exit 1; `--times` runs that disagree, on the route they
took or on the answer they came back with, are exit 3, because no run failed
and what broke is the claim that they agree; anything else is a bug in
bankbot and keeps its traceback. tests/test_cli.py covers
that judgement and nothing else here.

Does not own: the work. discover/, compile/, replay/ and control/ do it;
this file passes them to each other.

Governed by ADR-0006 (the browser is opened through the surface, never here)
and ADR-0003 (errors are named after the condition).
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from bankbot.compile import TranscriptNotCompilable, compile_capability
from bankbot.control import RunController, RunRegistry, create_operator_app
from bankbot.discover import (
    ClaudeDecider,
    Discovery,
    DiscoveryCouldNotStart,
    ModelKeyMissing,
    StopReason,
    Transcript,
    check_model_key,
    load_goal_spec,
)
from bankbot.evidence import (
    EvidenceWriter,
    RunDir,
    RunDirectoryExists,
    RunDirectoryMissing,
    event_sequence_hash,
    new_run_id,
    read_events,
)
from bankbot.evidence.verify import sequence_hashes, verify_evidence
from bankbot.policy import PolicyFileInvalid, PolicyFileMissing, load_policy
from bankbot.replay import ParamInvalid, ParamMissing, Replay, SecretMissing
from bankbot.replay.values import check_params, check_secrets
from bankbot.schemas import REPLAY_RESULT_ADAPTER, Capability, Success
from bankbot.surface import PlaywrightSurface, headed_requested, open_page
from bankbot.target import create_app, start_server

GOALS_DIR = Path(__file__).parent / "discover" / "goals"
DEFAULT_SPEC = GOALS_DIR / "lookup_savings_balance.json"
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_EVIDENCE_DIR = Path("evidence")
RECORDED_CAPABILITY = DEFAULT_EVIDENCE_DIR / "01-discovery" / "capability.json"
# Only the `operator` subcommand, which browses finished runs and is worth finding
# at the same address twice. A run's own operator page takes whatever port is free.
OPERATOR_PORT = 8765
VARIANT_PREFIXES = {"a": "", "b": "/b"}
TARGET_PROBE_TIMEOUT_S = 2.0
EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_CALLER_ERROR = 2
EXIT_RUNS_DISAGREE = 3


class CapabilityFileMissing(Exception):
    """There is no artifact file where the caller pointed."""


class CapabilityFileInvalid(Exception):
    """The artifact file is there but does not validate as a Capability."""


class TranscriptFileMissing(Exception):
    """There is no transcript where the caller pointed."""


class TranscriptFileInvalid(Exception):
    """The transcript file is there but does not validate as a Transcript."""


class TargetUnreachable(Exception):
    """Nothing answered at the --base-url the caller gave."""


class OptionMalformed(Exception):
    """A --param or --fault was not written as NAME=VALUE."""


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for `python -m bankbot.cli`; returns the process exit code.

    The conditions caught below are the ones a caller causes: a typo, a
    missing variable, a port with nothing behind it. Each of those is worth
    one line and no more.
    """
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "discover":
            return _discover(args)
        if args.command == "compile":
            return _compile(args)
        if args.command == "operator":
            return _operator(args)
        if args.command == "verify-evidence":
            return _verify(args)
        return _replay(args)
    except (
        CapabilityFileInvalid,
        CapabilityFileMissing,
        ModelKeyMissing,
        OptionMalformed,
        ParamInvalid,
        ParamMissing,
        PolicyFileInvalid,
        PolicyFileMissing,
        RunDirectoryExists,
        RunDirectoryMissing,
        SecretMissing,
        TargetUnreachable,
        TranscriptFileInvalid,
        TranscriptFileMissing,
        TranscriptNotCompilable,
    ) as condition:
        print(f"error: {condition}", file=sys.stderr)
        hint = _hint_for(condition)
        if hint is not None:
            print(f"hint: {hint}", file=sys.stderr)
        return EXIT_CALLER_ERROR
    except DiscoveryCouldNotStart as condition:
        # The run started and could not get going, which is a failed run and
        # not a rejected command; the run directory holds the evidence.
        print(f"error: {condition}", file=sys.stderr)
        return EXIT_RUN_FAILED
    # There is deliberately no catch-all. Anything not named above is a bug in
    # bankbot rather than a condition of the world, and a bug is worth a
    # traceback with the line number in it.


def _hint_for(condition: Exception) -> str | None:
    """A second line only where the next thing to type is obvious.

    A hint that guesses wrong sends a caller down the wrong path, so
    conditions without one fix (a bad policy file, say) get none.
    """
    if isinstance(condition, ParamMissing | ParamInvalid | OptionMalformed):
        return "inputs are passed as --param NAME=VALUE"
    if isinstance(condition, SecretMissing):
        return "copy .env.example to .env and fill in the value"
    if isinstance(condition, ModelKeyMissing):
        return f"replay needs no key: python -m bankbot.cli replay {RECORDED_CAPABILITY}"
    if isinstance(condition, CapabilityFileMissing):
        return f"the recorded capability is at {RECORDED_CAPABILITY}"
    if isinstance(condition, TranscriptFileMissing):
        return "point at a run directory, or at the transcript.json inside one"
    if isinstance(condition, RunDirectoryExists):
        return "pass a different --run-id, or delete that directory first"
    if isinstance(condition, TargetUnreachable):
        return "drop --base-url and bankbot starts the demo app itself"
    return None


def build_parser() -> argparse.ArgumentParser:
    """Discover and replay share the run options, so the README's demo lines stay short."""
    parser = argparse.ArgumentParser(prog="bankbot")
    commands = parser.add_subparsers(dest="command", required=True)

    discover_cmd = commands.add_parser("discover", help="let the model record a capability once")
    discover_cmd.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    discover_cmd.add_argument("--goal", help="override the spec's goal sentence")
    _add_run_options(discover_cmd)

    compile_cmd = commands.add_parser(
        "compile", help="re-make a capability from a saved run, with no model"
    )
    compile_cmd.add_argument(
        "run", type=Path, help="a run directory, or the transcript.json inside one"
    )
    compile_cmd.add_argument("--spec", type=Path, default=DEFAULT_SPEC)

    replay_cmd = commands.add_parser("replay", help="run a capability with no model")
    replay_cmd.add_argument("capability", type=Path)
    replay_cmd.add_argument("--fault", action="append", default=[], metavar="NAME=VALUE")
    replay_cmd.add_argument("--keep-trace", action="store_true")
    replay_cmd.add_argument("--variant", choices=sorted(VARIANT_PREFIXES), default="a")
    replay_cmd.add_argument(
        "--times",
        type=int,
        default=1,
        help="repeat in fresh browsers; runs that hash differently fail the command",
    )
    _add_run_options(replay_cmd)

    operator_cmd = commands.add_parser(
        "operator", help="browse finished runs in the operator pages"
    )
    operator_cmd.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    operator_cmd.add_argument("--port", type=int, default=OPERATOR_PORT)

    verify_cmd = commands.add_parser(
        "verify-evidence", help="check every evidence directory the way a reviewer would"
    )
    verify_cmd.add_argument("root", type=Path, nargs="?", default=DEFAULT_EVIDENCE_DIR)
    return parser


def _add_run_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    command.add_argument("--base-url", help="an already running target; default starts one")
    command.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    command.add_argument("--run-id", help="directory name under runs-dir; default is timestamped")


def _discover(args: argparse.Namespace) -> int:
    spec = load_goal_spec(args.spec)
    if args.goal:
        spec = spec.model_copy(update={"goal": args.goal})
    params = _parse_pairs(args.param)
    # Everything a caller can get wrong is checked before the run directory
    # exists and before a browser opens, so a rejected command leaves no
    # empty directory behind and costs no model call.
    check_params(spec.inputs, params)
    check_secrets([step for recovery in spec.recoveries for step in recovery.steps], os.environ)
    check_model_key(os.environ)
    policy = load_policy()
    _check_target_reachable(args.base_url)
    run_dir = RunDir.create(args.runs_dir, args.run_id or new_run_id())
    writer = EvidenceWriter(run_dir, policy.redactor(extra_values=params.values()))
    with running_target(args.base_url) as base_url, open_page() as page:
        transcript = Discovery(
            spec,
            params,
            surface=PlaywrightSurface(page, policy.mask_selectors),
            policy=policy,
            run_dir=run_dir,
            writer=writer,
            decider=ClaudeDecider(),
            base_url=base_url,
        ).run()
    print(f"stopped: {transcript.stop_reason.value} after {len(transcript.steps)} steps")
    print(f"tokens: {transcript.input_tokens} in, {transcript.output_tokens} out")
    print(f"run directory: {run_dir.path}")
    if transcript.stop_reason is not StopReason.DONE:
        return EXIT_RUN_FAILED
    capability = compile_capability(transcript, spec, secrets=os.environ)
    writer.save_model("capability", capability)
    print(f"capability: {run_dir.capability_path} ({len(capability.steps)} steps)")
    return EXIT_OK


def _compile(args: argparse.Namespace) -> int:
    """Re-make an artifact from a run that already happened. No key, no browser, no model.

    The transcript is the real run; the capability is one compiler's reading
    of it. Keeping those two files apart is what lets a fix to the locator
    rules reach the shipped artifact without paying for another model run,
    and it is how the committed artifact is checked against its own
    transcript rather than believed.

    One caveat, and it belongs to the file rather than to this command. A
    transcript on disk has been through the redactor, so a parameter value
    reads as the mask token everywhere it appears. The compiler decides a
    typed value is a parameter by comparing the two for equality, and with
    one input the two masks are equal, so the match still holds. With two
    inputs the masks would be equal to each other as well and the compiler
    could no longer tell which parameter was typed. Recompiling a
    multi-input run needs the unredacted transcript from the machine that
    recorded it.
    """
    spec = load_goal_spec(args.spec)
    pointed_at: Path = args.run
    run_dir = RunDir.open(pointed_at if pointed_at.is_dir() else pointed_at.parent)
    source = run_dir.transcript_path if pointed_at.is_dir() else pointed_at
    transcript = _load_transcript(source)
    capability = compile_capability(transcript, spec, secrets=os.environ)
    writer = EvidenceWriter(
        run_dir, load_policy().redactor(extra_values=transcript.params.values())
    )
    writer.save_model("capability", capability)
    print(f"capability: {run_dir.capability_path} ({len(capability.steps)} steps)")
    return EXIT_OK


def _replay(args: argparse.Namespace) -> int:
    capability = _load_capability(args.capability)
    params = _parse_pairs(args.param)
    faults = _parse_pairs(args.fault)
    check_params(capability.inputs, params)
    recovery_steps = [step for recovery in capability.recoveries for step in recovery.steps]
    check_secrets(capability.steps + recovery_steps, os.environ)
    policy = load_policy()
    _check_target_reachable(args.base_url)
    registry = RunRegistry()
    operator_started = False
    exit_code = EXIT_OK
    hashes: dict[str, str] = {}
    answers: dict[str, str] = {}
    with running_target(args.base_url) as target:
        base_url = target + VARIANT_PREFIXES[args.variant]
        for run_id in _run_ids(args.run_id or new_run_id(), args.times):
            run_dir = RunDir.create(args.runs_dir, run_id)
            writer = EvidenceWriter(run_dir, policy.redactor(extra_values=params.values()))
            # A fresh browser and freshly armed faults per run, so every run starts
            # from the same place; that is what makes the printed hashes comparable.
            with open_page() as page:
                arm_faults(target, faults)
                surface = PlaywrightSurface(page, policy.mask_selectors)
                controller: RunController | None = None
                if headed_requested():
                    # A person can see the browser, so a person can be asked. Headless
                    # runs stay unattended: nobody is there to take control.
                    controller = RunController(
                        run_dir, capability, sorted(params), surface=surface, writer=writer
                    )
                    registry.add(controller)
                    if not operator_started:
                        # Any free port. A handoff run holds its operator page open for
                        # as long as it waits for a person, so a fixed number means the
                        # next headed run dies on bind before the browser opens. The URL
                        # is printed on the line below; nothing has to guess it.
                        operator = start_server(create_operator_app(registry, args.runs_dir))
                        operator_started = True
                    print(f"operator page: {operator.base_url}/operator/{run_id}", file=sys.stderr)
                result = Replay(
                    capability,
                    params,
                    surface=surface,
                    policy=policy,
                    run_dir=run_dir,
                    writer=writer,
                    base_url=base_url,
                    escalation=controller,
                    keep_trace=args.keep_trace,
                ).run()
                if controller is not None:
                    controller.finish(result.kind)
            print(json.dumps(REPLAY_RESULT_ADAPTER.dump_python(result, mode="json"), indent=2))
            if isinstance(result, Success):
                answers[run_id] = json.dumps(result.outputs, sort_keys=True)
            hashes[run_id] = event_sequence_hash(read_events(run_dir))
            print(f"event sequence: {hashes[run_id]}")
            print(f"run directory: {run_dir.path}", file=sys.stderr)
            if not (isinstance(result, Success) or result.kind == "outcome"):
                exit_code = EXIT_RUN_FAILED
    # Two claims, checked separately, because the hash stopped covering the
    # second one: it says which route the run took, and the outputs say what it
    # came back with. Runs that read different balances off the same page log
    # the same events and hash the same. Route first, and only route, when both
    # disagree: a different route is why a different answer came back.
    if len(set(hashes.values())) > 1:
        _report_disagreement("did not do the same things", hashes)
    elif len(set(answers.values())) > 1:
        _report_disagreement("did not come back with the same answer", answers)
    else:
        return exit_code
    # A run that failed is a fact about one run and is the thing to fix
    # first, so it keeps the exit code when both are true.
    return EXIT_RUNS_DISAGREE if exit_code == EXIT_OK else exit_code


def _report_disagreement(what: str, by_run: dict[str, str]) -> None:
    print(f"error: these runs {what}", file=sys.stderr)
    for run_id, value in by_run.items():
        print(f"  {run_id}: {value}", file=sys.stderr)


def _run_ids(first: str, times: int) -> list[str]:
    """`--times 5` with run id X writes X, X-2, X-3, X-4, X-5, so the runs sort together."""
    return [first, *(f"{first}-{n}" for n in range(2, times + 1))]


def _verify(args: argparse.Namespace) -> int:
    """Hashes for every run, then every problem; a non-zero exit is the pre-commit signal."""
    for run_id, digest in sequence_hashes(args.root).items():
        print(f"{run_id}: {digest}")
    problems = verify_evidence(args.root, load_policy().redactor())
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{len(problems)} problems in {args.root}", file=sys.stderr)
    return EXIT_RUN_FAILED if problems else EXIT_OK


def _operator(args: argparse.Namespace) -> int:
    """Read-only browsing of finished runs; live control only exists inside a replay process."""
    handle = start_server(create_operator_app(RunRegistry(), args.runs_dir), args.port)
    print(f"operator pages at {handle.base_url}/operator (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        handle.stop()
    return EXIT_OK


def _load_capability(path: Path) -> Capability:
    """Read the artifact before anything else, so a wrong path costs a line and not a run."""
    try:
        text = path.read_text()
    except OSError as error:
        raise CapabilityFileMissing(
            f"cannot read the capability at {path}: {error.strerror}"
        ) from error
    try:
        return Capability.model_validate_json(text)
    except ValidationError as error:
        # The first problem only: a caller fixes one field at a time, and the
        # whole pydantic report is pages long for a file that is not JSON.
        problem = error.errors()[0]
        where = ".".join(str(part) for part in problem["loc"])
        detail = f"{where}: {problem['msg']}" if where else str(problem["msg"])
        raise CapabilityFileInvalid(f"{path} is not a capability: {detail}") from error


def _load_transcript(path: Path) -> Transcript:
    """Read the saved run before anything else, for the same reason _load_capability does."""
    try:
        text = path.read_text()
    except OSError as error:
        raise TranscriptFileMissing(
            f"cannot read the transcript at {path}: {error.strerror}"
        ) from error
    try:
        return Transcript.model_validate_json(text)
    except ValidationError as error:
        problem = error.errors()[0]
        where = ".".join(str(part) for part in problem["loc"])
        detail = f"{where}: {problem['msg']}" if where else str(problem["msg"])
        raise TranscriptFileInvalid(f"{path} is not a transcript: {detail}") from error


def _check_target_reachable(base_url: str | None) -> None:
    """Ask the port a question before the run, because a dead port is not a step failure.

    Nothing to probe without --base-url: running_target starts the demo app
    in this process and that cannot be unreachable. Any answer at all
    counts, including a 404; the question is whether someone is there.
    """
    if not base_url:
        return
    try:
        httpx.get(base_url, timeout=TARGET_PROBE_TIMEOUT_S)
    except (httpx.InvalidURL, httpx.RequestError) as error:
        raise TargetUnreachable(f"nothing answered at {base_url}: {error}") from error


@contextmanager
def running_target(base_url: str | None) -> Iterator[str]:
    """Use the target you point at, or start the demo app in this process for the run."""
    if base_url:
        yield base_url
        return
    handle = start_server(create_app())
    try:
        yield handle.base_url
    finally:
        handle.stop()


def arm_faults(base_url: str, faults: dict[str, str]) -> None:
    """Faults are per run: set them on the target right before replay starts, or clear them."""
    body = {name: int(value) for name, value in faults.items()}
    httpx.post(f"{base_url}/admin/faults", json=body).raise_for_status()


def _parse_pairs(pairs: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            # SystemExit here used to end the process at 1, which is the code
            # for a run that failed. Nothing has run yet, so it is a 2.
            raise OptionMalformed(f"expected NAME=VALUE, got {pair!r}")
        name, value = pair.split("=", 1)
        values[name] = value
    return values


if __name__ == "__main__":
    sys.exit(main())
