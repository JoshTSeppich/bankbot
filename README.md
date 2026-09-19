# bankbot

Record a browser workflow once with an LLM watching, then replay it without one. The recording is a typed artifact that names each control the way a person sees it, so it can be reviewed, versioned and rerun against the same app. This is my take-home submission for interface.ai.

## Run

```
uv sync
uv run pre-commit install
uv run pytest
```

## Demo path

Not built yet. One capability, `lookup_savings_balance(member_id)`, against a bank app I wrote to be hard to automate. Five runs: discovery, replay success, member not found, session expiry recovered, human handoff and resume.

## What is mocked and why

The bank app is my own FastAPI app, not a vendor product. I built it so I can trigger every failure the brief names on demand (table layouts, one iframe, no ids, fault-injection endpoints) without a real bank, real member data or a terms-of-service problem. The operator page is a bare HTML page in the same process. The mechanism behind it is real; the page is not a product.

## Where to read more

- docs/adr/: the decisions the design rests on.
- docs/schema/capability.schema.json: the artifact contract.
- REPORT.md: the write-up. Not written yet.
