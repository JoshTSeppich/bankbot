# Every demo path in the README is one of these targets. The target app runs
# in the same process as the CLI, so nothing needs starting first.
CAP      ?= evidence/01-discovery/capability.json
CLI       = uv run python -m bankbot.cli
EVIDENCE  = --runs-dir evidence --keep-trace

.PHONY: test lint discover replay replay-5 replay-notfound replay-expiry replay-dialog replay-variant-b \
        operator demo verify-evidence \
        evidence-01 evidence-02 evidence-03 evidence-04 evidence-05 evidence-06 evidence-07

# A fresh clone has no .env, and the demo credentials live in it, so every
# target below that runs the CLI depends on this file existing. Not .PHONY:
# make runs the copy only when the file is missing, so an .env you edited is
# never overwritten. Echoed rather than silenced, because a target that writes
# a file into the working tree should say so. The defaults are the ones in
# .env.example and they are public by design; nothing moves into code, and the
# CLI still reads every secret from the environment.
.env:
	cp .env.example .env

test:
	uv run pytest

lint:
	uv run ruff format --check && uv run ruff check && uv run mypy --strict

# Needs ANTHROPIC_API_KEY in .env. discover, evidence-01 and evidence-06 are
# the only targets that talk to a model.
discover: .env
	$(CLI) discover --goal "look up the savings balance for the member" --param member_id=M-100

# The rest never touch a model. They run the compiled capability.
replay: .env
	$(CLI) replay $(CAP) --param member_id=M-100

# The determinism claim run rather than asserted: five fresh browsers, one
# hash printed five times. evidence/02-replay-success* is a recorded instance.
replay-5: .env
	$(CLI) replay $(CAP) --param member_id=M-100 --times 5

replay-notfound: .env
	$(CLI) replay $(CAP) --param member_id=M-999

replay-expiry: .env
	$(CLI) replay $(CAP) --param member_id=M-100 --fault session_expiry_at_step=3

# Pauses for a human: open the operator page it prints, dismiss the dialog, hand back.
replay-dialog: .env
	HEADED=1 $(CLI) replay $(CAP) --param member_id=M-100 --fault unknown_dialog_at_step=2

replay-variant-b: .env
	$(CLI) replay $(CAP) --param member_id=M-100 --variant b

operator:
	$(CLI) operator

demo: discover replay replay-notfound replay-expiry

# The seven evidence runs, written under evidence/ with their traces kept. A
# run directory is never overwritten: delete it first to redo a run.
evidence-01: .env
	$(CLI) discover --goal "look up the savings balance for the member" --param member_id=M-100 \
	  --runs-dir evidence --run-id 01-discovery

evidence-02: .env
	$(CLI) replay $(CAP) --param member_id=M-100 --times 5 $(EVIDENCE) --run-id 02-replay-success

evidence-03: .env
	$(CLI) replay $(CAP) --param member_id=M-999 $(EVIDENCE) --run-id 03-replay-member-not-found

evidence-04: .env
	$(CLI) replay $(CAP) --param member_id=M-100 --fault session_expiry_at_step=3 \
	  $(EVIDENCE) --run-id 04-replay-session-expiry-recovered

evidence-05: .env
	HEADED=1 $(CLI) replay $(CAP) --param member_id=M-100 --fault unknown_dialog_at_step=2 \
	  $(EVIDENCE) --run-id 05-replay-handoff

evidence-06: .env
	$(CLI) discover --spec bankbot/discover/goals/close_member_account.json --param member_id=M-100 \
	  --runs-dir evidence --run-id 06-discovery-risky-blocked

evidence-07: .env
	$(CLI) replay $(CAP) --param member_id=M-100 --variant b $(EVIDENCE) --run-id 07-replay-variant-b

# Every evidence directory parses, names only known events, points at
# screenshots that exist, and carries no secret or PII-shaped value.
verify-evidence: .env
	$(CLI) verify-evidence evidence
