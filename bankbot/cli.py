"""The command line: discover, replay. Wiring only; every decision lives in a module.

Owns: argument parsing, starting the demo app in-process when no base URL is
given, opening the browser, and printing the result. Nothing here decides
anything a test would need to cover, which is why it has none of its own.

Does not own: any logic. discover/, compile/, replay/ and control/ do the
work; this file passes them to each other.

Governed by ADR-0006 (the browser is opened through the surface, never here).
"""

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv

from bankbot.discover import ClaudeDecider, Discovery, load_goal_spec
from bankbot.evidence import EvidenceWriter, RunDir, new_run_id
from bankbot.policy import load_policy
from bankbot.replay import Replay
from bankbot.schemas import REPLAY_RESULT_ADAPTER, Capability, Success
from bankbot.surface import PlaywrightSurface, open_page
from bankbot.target import create_app, start_server

GOALS_DIR = Path(__file__).parent / "discover" / "goals"
DEFAULT_SPEC = GOALS_DIR / "lookup_savings_balance.json"
DEFAULT_RUNS_DIR = Path("runs")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for `python -m bankbot.cli`; returns the process exit code."""
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.command == "discover":
        return _discover(args)
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
    _add_run_options(replay_cmd)
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
    return 0 if transcript.stop_reason.value == "done" else 1


def _replay(args: argparse.Namespace) -> int:
    capability = Capability.model_validate_json(args.capability.read_text())
    params = _parse_pairs(args.param)
    policy = load_policy()
    run_dir = RunDir.create(args.runs_dir, args.run_id or new_run_id())
    writer = EvidenceWriter(run_dir, policy.redactor(extra_values=params.values()))
    with running_target(args.base_url) as base_url, open_page() as page:
        arm_faults(base_url, _parse_pairs(args.fault))
        result = Replay(
            capability,
            params,
            surface=PlaywrightSurface(page, policy.mask_selectors),
            policy=policy,
            run_dir=run_dir,
            writer=writer,
            base_url=base_url,
            keep_trace=args.keep_trace,
        ).run()
    print(json.dumps(REPLAY_RESULT_ADAPTER.dump_python(result, mode="json"), indent=2))
    print(f"run directory: {run_dir.path}", file=sys.stderr)
    return 0 if isinstance(result, Success) or result.kind == "outcome" else 1


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
