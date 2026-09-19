# Every demo path in the README is one of these targets. The target app runs
# in the same process as the CLI, so nothing needs starting first.
CAP     ?= evidence/01-discovery/capability.json
CLI      = uv run python -m bankbot.cli

.PHONY: test lint discover replay replay-notfound replay-expiry replay-dialog operator demo

test:
	uv run pytest

lint:
	uv run ruff format --check && uv run ruff check && uv run mypy --strict

# Needs ANTHROPIC_API_KEY in .env. The only target that talks to a model.
discover:
	$(CLI) discover --goal "look up the savings balance for the member" --param member_id=M-100

# The rest never touch a model. They run the compiled capability.
replay:
	$(CLI) replay $(CAP) --param member_id=M-100

replay-notfound:
	$(CLI) replay $(CAP) --param member_id=M-999

replay-expiry:
	$(CLI) replay $(CAP) --param member_id=M-100 --fault session_expiry_at_step=3

# Pauses for a human: open the operator page it prints, dismiss the dialog, resume.
replay-dialog:
	HEADED=1 $(CLI) replay $(CAP) --param member_id=M-100 --fault unknown_dialog_at_step=3

operator:
	$(CLI) operator

demo: discover replay replay-notfound replay-expiry
