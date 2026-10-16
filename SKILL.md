---
name: ai-test-loop
description: "Use before declaring any work done or shipped, when the agent must verify its own output, when a deliverable needs a score before release, when the builder keeps approving its own work, or when repeated review rounds never converge. Runs an independent critic that scores the artifact 0-100 with real checks, loops until the threshold, and blocks delivery on a fail-closed security gate."
---

# AI Test Loop — never ship unverified work

The builder is not allowed to grade its own homework. This skill turns "looks done" into
"verified, scored, and independently reviewed" — the loop either reaches the threshold with
evidence, or it reports exactly which gaps remain.

Use it whenever an agent produced something a human will rely on: code, UI, a pipeline, a
document, a data migration. Not for throwaway exploration.

## The four roles (never merge them)

| Role | Who | Job |
|---|---|---|
| Orchestrator | the agent running the task | picks checks, runs rounds, fixes infrastructure, owns the report |
| Builder | the agent/model that wrote the artifact | fixes gaps, one class of gap per pass |
| Critic | a **script you write** (`loop_runner.py` + your checks) | runs real commands, returns a 0-100 score and a gap list |
| Reviewer | a **separate** agent/model context | audits critic + builder, votes, can reopen the loop |

Rule zero: builder ≠ critic ≠ reviewer. If the same context does all three, the loop is theatre.
Run steps 1-3 below in the working session, then drive the script for the machine part.

## The loop

1. **Declare the checks before writing code.** For each check: name, command, weight, and what
   "pass" means. Weights must sum to 100.
2. **Run the critic** — `python scripts/loop_runner.py --config loop.json --round N`. It executes
   every command for real, records raw output as evidence, and prints `SCORE=<0-100>` and
   `GAPS=[...]`. It never asks a model whether the work is good; it measures.
3. **Score, compare, decide.** `SCORE >= threshold` → go to the reviewer. Otherwise pick the
   **single largest** gap, fix it, and re-run. Cap the rounds (8 is plenty); if it never
   converges, the check set is wrong, not the code.
4. **Independent review** before delivery — same artifact, fresh context, no justification from
   the builder: `references/reviewer-prompt.md`. The reviewer returns strict JSON.
5. **Security gate, fail-closed.** Any secret in the diff/history, any high-severity SAST finding,
   any auth/path-traversal/injection issue found by review blocks delivery even with perfect
   tests. Treat an inconclusive answer as blocked, never as pass.
6. **Report** with the score, the evidence, the reviewer verdict, and what was *not* verifiable.

## Scoring

Weights are yours to set; start from `references/scoring-rubric.md`. Non-negotiables:

- Every point must be earned by a command that actually ran. No points for "read the code".
- Evidence is captured, not summarised: keep the raw output next to the score.
- If a check cannot run (no environment, no device, dependency missing), it scores 0 and the
  report says `NOT VERIFIABLE: <reason>` — never silently skipped, never counted as passing.

## Reviewer panel (for anything that will be used by others)

Ask 2-3 specialists instead of one generalist, each returning strict JSON:
`{"parecer":"CONCORDO|DISCORDO","nota":0-100,"gaps":["..."],"texto":"one paragraph with evidence"}`.

- Profiles by nature of the work: quality (always), security (always), performance (only if the
  artifact has a build or a runtime cost), UX (only if there is a user-facing surface).
- **Consensus** → deliver. **Majority disagree** → reopen the loop with the combined gaps.
  **Security disagrees or is inconclusive** → block. **Tie 1-1** → a third specialist decides.
- Do not send an empty `gaps` array as a formality: a reviewer that finds nothing must still cite
  the evidence it inspected.

## Pitfalls (each one measured in production)

- **Template echo.** A model asked to answer with an enum often echoes the enum back
  (`"CONCORDO|DISCORDO"`). Reject any value that matches the literal template, treat it as
  inconclusive, retry once, then fail closed. A parser that accepts the echo reopens the loop
  forever with a false gap.
- **Piping hides the exit code.** `pytest | tail` returns the exit code of `tail` (0) — the critic
  scores 100 with a failing test hidden in the output. Use `set -o pipefail` (or read exit codes
  without a pipe) inside every check.
- **A timeout shorter than the check is a silent zero.** When the critic is killed by its own
  timeout, the orchestrator sees a score of 0 with an empty gap list and loops with nothing to fix.
  Set the timeout above the real duration and always keep the critic's raw log.
- **A short-circuiting "pass" is not a pass.** A server that responds 200 may be the wrong
  server: on a port collision the check tests a different app and "fails" for the wrong reason.
  Assert identity, not reachability (title, version, a known payload).
- **Stale baselines.** A visual or snapshot baseline regenerated "to make it green" erases the
  regression. Regenerating is allowed only when the change was intended — and it must be stated
  in the report, with the diff before and after.
- **Cold start.** First call to a cold service can take seconds; re-test before declaring a
  timeout, and record that you retested.
- **Fixing many gap classes at once.** One worker per gap class keeps the next verdict readable.
  Batch everything and you cannot tell which change moved the score.

## Output format

```
ROUND: <n>   SCORE: <0-100>   THRESHOLD: <t>   GATE: <fail-closed | passed>
CHECKS: <name> PASS|FAIL <weight> — <one line of real evidence>
GAPS: <sorted by weight, biggest first>
REVIEWER: <CONCORDO|DISCORDO> <nota> — <evidence cited> | panel: q=… s=… p=… u=…
SECURITY: <clean | BLOCKED: <finding>> | INCONCLUSIVE → blocked
NOT VERIFIABLE: <check — reason>
NEXT: <single next action>
```

## Scope and network access

Runs only the commands you put in the config, on your machine. No network calls of its own, no
telemetry, no uploads; the security step greps the repo locally. It never deploys and never
merges: delivery decisions stay with the human owner.

## Files

- `scripts/loop_runner.py` — the critic: runs weighed checks, scores 0-100, prints gaps, keeps state.
- `templates/loop.example.json` — a ready-to-edit config; copy it next to the project and adjust.
- `references/scoring-rubric.md` — starting weights per artifact type.
- `references/reviewer-prompt.md` — the strict-JSON reviewer prompt plus the anti-echo parser rules.
