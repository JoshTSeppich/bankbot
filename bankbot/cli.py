"""The command line: discover, replay, operator, verify-evidence. Wiring only.

Owns: argument parsing, starting the demo app in-process when no base URL is
given, opening the browser, starting the operator pages when a person can
see the browser (HEADED=1), and printing the result. Nothing here decides
anything a test would need to cover, which is why it has none of its own.

Does not own: any logic. discover/, compile/, replay/ and control/ do the
work; this file passes them to each other.

Governed by ADR-0006 (the browser is opened through the surface, never here).
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

from bankbot.compile import compile_capability
from bankbot.control import RunController, RunRegistry, create_operator_app
from bankbot.discover import ClaudeDecider, Discovery, StopReason, load_goal_spec
from bankbot.evidence import EvidenceWriter, RunDir, event_sequence_hash, new_run_id, read_events
from bankbot.evidence.verify import sequence_hashes, verify_evidence
from bankbot.policy import load_policy
from bankbot.replay import Replay
from bankbot.schemas import REPLAY_RESULT_ADAPTER, Capability, Success
from bankbot.surface import PlaywrightSurface, headed_requested, open_page
from bankbot.target import create_app, start_server

GOALS_DIR = Path(__file__).parent / "discover" / "goals"
DEFAULT_SPEC = GOALS_DIR / "lookup_savings_balance.json"
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_EVIDENCE_DIR = Path("evidence")
OPERATOR_PORT = 8765
VARIANT_PREFIXES = {"a": "", "b": "/b"}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for `python -m bankbot.cli`; returns the process exit code."""
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.command == "discover":
        return _discover(args)
    if args.command == "operator":
        return _operator(args)
    if args.command == "verify-evidence":
        return _verify(args)
    return _replay(args)


def build_parser() -> argparse.ArgumentParser:
    """Two commands with the same run options, so the README's demo lines stay short."""
    parser = argparse.ArgumentParser(prog="bankbot")
    commands = parser.add_subparsers(dest="command", required=True)

    discover_cmd = commands.add_parser("discover", help="let the model record a capability once")
    discover_cmd.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    discover_cmd.add_argument("--goal", help="override the spec's goal sentence")
    _add_run_options(discover_cmd)

    replay_cmd = commands.add_parser("replay", help="run a capability with no model")
    replay_cmd.add_argument("capability", type=Path)
    replay_cmd.add_argument("--fault", action="append", default=[], metavar="NAME=VALUE")
    replay_cmd.add_argument("--keep-trace", action="store_true")
    replay_cmd.add_argument("--variant", choices=sorted(VARIANT_PREFIXES), default="a")
    replay_cmd.add_argument(
        "--times", type=int, default=1, help="repeat in fresh browsers; prints one hash per run"
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
    policy = load_policy()
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
        return 1
    capability = compile_capability(transcript, spec, secrets=os.environ)
    writer.save_model("capability", capability)
    print(f"capability: {run_dir.capability_path} ({len(capability.steps)} steps)")
    return 0


def _replay(args: argparse.Namespace) -> int:
    capability = Capability.model_validate_json(args.capability.read_text())
    params = _parse_pairs(args.param)
    faults = _parse_pairs(args.fault)
    policy = load_policy()
    registry = RunRegistry()
    operator_started = False
    exit_code = 0
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
                        operator = start_server(
                            create_operator_app(registry, args.runs_dir), OPERATOR_PORT
                        )
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
            print(f"event sequence: {event_sequence_hash(read_events(run_dir))}")
            print(f"run directory: {run_dir.path}", file=sys.stderr)
            if not (isinstance(result, Success) or result.kind == "outcome"):
                exit_code = 1
    return exit_code


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
    return 1 if problems else 0


def _operator(args: argparse.Namespace) -> int:
    """Read-only browsing of finished runs; live control only exists inside a replay process."""
    handle = start_server(create_operator_app(RunRegistry(), args.runs_dir), args.port)
    print(f"operator pages at {handle.base_url}/operator (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        handle.stop()
    return 0


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
            raise SystemExit(f"expected NAME=VALUE, got {pair!r}")
        name, value = pair.split("=", 1)
        values[name] = value
    return values


if __name__ == "__main__":
    sys.exit(main())
