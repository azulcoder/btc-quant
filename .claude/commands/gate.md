---
description: Run make gate and report the output verbatim; never fix a red gate unprompted
allowed-tools: Bash(make gate)
---

Run `make gate` and paste its output VERBATIM — including failures, tracebacks,
and exit codes. Do not summarize, trim, or paraphrase a failure.

If any gate goes red:

1. Name which gate failed — pytest, check_parity, check_terminal,
   churn-threshold, or lockbox-integrity (the recipe is fail-fast, so the last
   command printed is the one that failed).
2. Stop. Never fix a failure without asking first.

Do not re-run the gate to "see if it passes this time", and do not run any
other command as part of this slash command.
