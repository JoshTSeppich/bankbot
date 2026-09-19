# Every demo path in the README is one of these targets. The target app runs
# in the same process as the CLI, so nothing needs starting first.
CAP      ?= evidence/01-discovery/capability.json
CLI       = uv run python -m bankbot.cli
EVIDENCE  = --runs-dir evidence --keep-trace

.PHONY: test lint discover replay replay-notfound replay-expiry replay-dialog replay-variant-b \
        operator demo verify-evidence \
        evidence-01 evidence-02 evidence-03 evidence-04 evidence-05 evidence-06 evidence-07

test:
	uv run pytest

lint:
	uv run ruff format --check && uv run ruff check && uv run mypy --strict

# Needs ANTHROPIC_API_KEY in .env. discover, evidence-01 and evidence-06 are
# the only targets that talk to a model.
discover:
	$(CLI) discover --goal "look up the savings balance for the member" --param member_id=M-100

# The rest never touch a model. They run the compiled capability.
replay:
	$(CLI) replay $(CAP) --param member_id=M-100

replay-notfound:
	$(CLI) replay $(CAP) --param member_id=M-999

replay-expiry:
	$(CLI) replay $(CAP) --param member_id=M-100 --fault session_expiry_at_step=3

# Pauses for a human: open the operator page it prints, dismiss the dialog, hand back.
replay-dialog:
	HEADED=1 $(CLI) replay $(CAP) --param member_id=M-100 --fault unknown_dialog_at_step=3

replay-variant-b:
	$(CLI) replay $(CAP) --param member_id=M-100 --variant b

operator:
	$(CLI) operator

demo: discover replay replay-notfound replay-expiry

# The seven evidence runs, written under evidence/ with their traces kept. A
# run directory is never overwritten: delete it first to redo a run.
evidence-01:
	$(CLI) discover --goal "look up the savings balance for the member" --param member_id=M-100 \
	  --runs-dir evidence --run-id 01-discovery

evidence-02:
	$(CLI) replay $(CAP) --param member_id=M-100 --times 5 $(EVIDENCE) --run-id 02-replay-success

evidence-03:
	$(CLI) replay $(CAP) --param member_id=M-999 $(EVIDENCE) --run-id 03-replay-member-not-found

evidence-04:
	$(CLI) replay $(CAP) --param member_id=M-100 --fault session_expiry_at_step=3 \
	  $(EVIDENCE) --run-id 04-replay-session-expiry-recovered

evidence-05:
	HEADED=1 $(CLI) replay $(CAP) --param member_id=M-100 --fault unknown_dialog_at_step=3 \
	  $(EVIDENCE) --run-id 05-replay-handoff

evidence-06:
	$(CLI) discover --spec bankbot/discover/goals/close_member_account.json --param member_id=M-100 \
	  --runs-dir evidence --run-id 06-discovery-risky-blocked

evidence-07:
	$(CLI) replay $(CAP) --param member_id=M-100 --variant b $(EVIDENCE) --run-id 07-replay-variant-b

# Every evidence directory parses, names only known events, points at
# screenshots that exist, and carries no secret or PII-shaped value.
verify-evidence:
	$(CLI) verify-evidence evidence
