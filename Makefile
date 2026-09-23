# Every demo path in the README is one of these targets. The target app runs
# in the same process as the CLI, so nothing needs starting first.
CAP      ?= evidence/01-discovery/capability.json
CLI       = uv run python -m bankbot.cli
EVIDENCE  = --runs-dir evidence --keep-trace

.PHONY: review test lint discover replay replay-notfound replay-expiry replay-dialog replay-variant-b \
        operator demo verify-evidence \
        evidence-01 evidence-02 evidence-03 evidence-04 evidence-05 evidence-05b evidence-06 evidence-07 evidence-08

# A fresh clone has no .env, and the demo credentials live in it, so every
# target below that runs the CLI depends on this file existing. Not .PHONY:
# make runs the copy only when the file is missing, so an .env you edited is
# never overwritten. Echoed rather than silenced, because a target that writes
# a file into the working tree should say so. The defaults are the ones in
# .env.example and they are public by design; nothing moves into code, and the
# CLI still reads every secret from the environment.
.env:
	cp .env.example .env

# One command for a reviewer with a fresh clone and no API key. It runs
# everything the pre-commit hook runs, then one replay, because a suite that
# passes is not the same claim as the system working. `replay` needs no key.
review: lint test verify-evidence replay

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

# The nine evidence runs, written under evidence/ with their traces kept.
#
# Order matters. 01 compiles the capability the other seven replay, so it goes
# first and 06 goes last; 01 and 06 are also the only two that call a model
# and cost money. A run directory is never overwritten, so a redo is two
# commands:
#
#     rm -rf evidence/03-replay-member-not-found && make evidence-03
#
# and the whole set is:
#
#     rm -rf evidence && make evidence-01 evidence-02 evidence-03 \
#         evidence-04 evidence-05 evidence-05b evidence-07 evidence-08 \
#         evidence-06 && make verify-evidence
#
# Rebuilding the artifact alone needs no key and no model call, because the
# transcript of the recorded run is committed beside it:
#
#     uv run python -m bankbot.cli compile evidence/01-discovery
#
# evidence-05 and evidence-05b need a person. Each opens a browser window and
# prints an operator URL. evidence-05 is the mark-complete-then-abort run: what
# the committed run records is Take control, Mark step complete, the engine
# checking the step's wait_for and disagreeing, then Take control and Abort.
# evidence-05b needs a person to press Take control, click OK in the browser
# window, and press Hand back. README has both sequences.
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

evidence-05b: .env
	HEADED=1 $(CLI) replay $(CAP) --param member_id=M-100 --fault unknown_dialog_at_step=2 \
	  $(EVIDENCE) --run-id 05b-replay-handoff-resumed

evidence-08: .env
	$(CLI) replay $(CAP) --param member_id=M-101 $(EVIDENCE) --run-id 08-replay-success-m101

# Every evidence directory parses, names only known events, points at
# screenshots that exist, and carries no secret or PII-shaped value.
verify-evidence: .env
	$(CLI) verify-evidence evidence
