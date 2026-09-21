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

To run discovery, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`. Every other variable has a working default.

## Demo path

Discovery, with the key. The model drives the demo app once and the run compiles to a capability:

```
make discover
```

Replay, without the key. The capability is executed with no model in the loop. The committed one from evidence run 1 is the default, so this works on a fresh clone:

```
make replay                 # success, prints outputs and the event-sequence hash
make replay-notfound        # member M-999: Outcome member_not_found, not a failure
make replay-expiry          # session expires mid-run: recovery, then success
make replay-variant-b       # the second tenant's build: drift_warning, variant_mismatch, success
make replay-dialog          # headed; pauses on an unknown dialog and prints the operator page
```

`make demo` runs discovery then the first three replays. `make verify-evidence` checks every directory under `evidence/` the way a reviewer would. `make operator` browses finished runs.

For the handoff, `replay-dialog` prints an operator URL. Open it, press Take control, dismiss the dialog in the browser window that is already open, press Hand back. The run finishes and the page shows what you did.

## What is mocked and why

The bank app is my own FastAPI app, `bankbot/target/`, not a vendor product. I wrote it so I could trigger every condition the brief names on demand (table layouts, an iframe, no ids, a session that expires, a dialog nobody has seen) without a real bank, real member data or a terms-of-service problem. It has a second variant under `/b` that stands in for another tenant's build.

The operator page is two Jinja pages in the same process as the run. There is one operator and no login. The handoff mechanism under it is real: same browser, same session, actions recorded, a lease that ends the run if the operator's page goes away.

Nothing else is mocked. The discovery run in `evidence/01-discovery` is a real model call, with the request ids in the artifact.

## Where to read more

- REPORT.md: the write-up under the seven headings from the brief.
- docs/adr/: the six decisions the design rests on, with what I rejected and why.
- docs/schema/capability.schema.json: the artifact contract.
- evidence/: seven runs, one per condition the brief names, in eleven directories because run 2 is replayed five times. Logs, screenshots and traces in each.
- github.com/JoshTSeppich/cairn: the working discipline this was built under, as a Claude Code plugin.
- github.com/JoshTSeppich/Lantern: the screen-shape method `surface/fingerprint.py` ports.
