# bankbot

[![ci](https://github.com/JoshTSeppich/bankbot/actions/workflows/ci.yml/badge.svg)](https://github.com/JoshTSeppich/bankbot/actions/workflows/ci.yml)

Record a browser workflow once with an LLM driving, then replay it without one. The recording is a typed artifact that names each control the way a person sees it, so it can be reviewed, versioned and rerun against the same app. This is my take-home submission for interface.ai; the write-up is in REPORT.md.

## Run

```
uv sync
uv run playwright install chromium
uv run pre-commit install
uv run pytest
```

The tests start the demo bank app and a headless Chromium themselves. Nothing else needs to be running. No key is needed for anything except discovery.

`make review` is the whole gate in one command: format, lint, `mypy --strict`, the test suite, the evidence check, and one replay against the committed capability. It is what runs before every commit and it needs no key.

To run discovery, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`. Every other variable has a working default.

## Demo path

Discovery, with the key. The model drives the demo app once and the run compiles to a capability:

```
make discover
```

Replay, without the key. The capability is executed with no model in the loop. The committed one from evidence run 1 is the default, so this works on a fresh clone:

```
make replay                 # "kind": "success", outputs {"savings_balance": "4242.00"}
make replay-notfound        # "kind": "outcome", "code": "member_not_found" — an answer, not a crash
make replay-expiry          # recoveries_used [session_expired, session_expired], then success
make replay-variant-b       # warnings variant_mismatch and drift_warning, then success
make replay-dialog          # headed; stops on an unknown modal and prints an operator URL
```

Each prints its result as JSON and then `event sequence: sha256:…`. `make demo` runs discovery and the first three replays; `make verify-evidence` checks every directory under `evidence/` the way a reviewer would; `make operator` browses finished runs.

For the handoff, `replay-dialog` prints an operator URL. Open it, press Take control, dismiss the modal in the browser window that is already open, press Hand back. The run finishes and the page shows what you did.

### What each run proves

Every row is a committed directory with its log, screenshots and trace. `make verify-evidence` recomputes the hashes.

| Scenario | Command | Evidence | What it proves |
|---|---|---|---|
| Discovery, a real model call | `make evidence-01` | `evidence/01-discovery` | The model drives a live UI to the goal and the run compiles to `capability.json`, with the seven API request ids in it |
| Replay, five times | `make evidence-02` | `evidence/02-replay-success` and `-2` to `-5` | Determinism: one event-sequence hash and one answer across five fresh browsers |
| A member who does not exist | `make evidence-03` | `evidence/03-replay-member-not-found` | A known business outcome is a result, not a failure |
| Session expiry mid-run | `make evidence-04` | `evidence/04-replay-session-expiry-recovered` | A declared recovery runs, the rewind re-logs in, the run still succeeds |
| An unknown modal, with an operator | `make evidence-05` | `evidence/05-replay-handoff` | A person takes the live session, claims the step, and the engine checks its `wait_for`, disagrees and asks again |
| A risky goal | `make evidence-06` | `evidence/06-discovery-risky-blocked` | The policy blocks an irreversible action the model proposed, and nothing compiles |
| A second tenant's build | `make evidence-07` | `evidence/07-replay-variant-b` | One artifact on a different build: two drift warnings, still success |

`evidence-01` and `evidence-06` are the only commands here that call a model. Rebuilding the artifact without one is `uv run python -m bankbot.cli compile evidence/01-discovery`. A run directory is never overwritten, so redoing one means deleting it first; the Makefile has the exact commands and the order.

## What is mocked and why

The bank app is my own FastAPI app, `bankbot/target/`, not a vendor product. I wrote it so I could trigger every condition the brief names on demand (table layouts, an iframe, no ids, a session that expires, a dialog nobody has seen) without a real bank, real member data or a terms-of-service problem. It has a second variant under `/b` that stands in for another tenant's build.

The operator page is two Jinja pages in the same process as the run. There is one operator and no login. The handoff mechanism under it is real: same browser, same session, actions recorded, a lease that ends the run if the operator's page goes away.

Nothing else is mocked. The discovery run in `evidence/01-discovery` is a real model call, with the request ids in the artifact.

## Where to read more

- REPORT.md: the write-up under the seven headings from the brief.
- docs/adr/: the six decisions the design rests on, with what I rejected and why.
- docs/schema/capability.schema.json: the artifact contract.
- evidence/: the seven runs in the table above, in eleven directories because run 2 is replayed five times.
- github.com/JoshTSeppich/cairn: the working discipline this was built under, as a Claude Code plugin.
- github.com/JoshTSeppich/Lantern: the screen-shape method `surface/fingerprint.py` ports.
