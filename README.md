# ai-test-loop

*[Português](README.pt-BR.md)*

**Your agent grades its own homework.** This makes that impossible: a critic script runs real checks,
scores the artifact 0–100, and a fail-closed gate blocks delivery below the threshold.

```bash
npx ai-test-loop --config loop.json     # zero install, no Python needed
```

**Using Claude Code?** Install it as a plugin — the skill loads automatically
when the agent is about to declare work done:

```
/plugin install ai-test-loop --marketplace Samuelfmedeiros/ai-test-loop
```

**Just want a score for your project?** Paste the path or the GitHub URL — no config file:

```bash
npx ai-test-loop --repo https://github.com/you/your-project
```

Real output from a project that had no config at all (the CLI prints in Portuguese, pt-BR, today — an English output mode is not shipped yet):

```
  PASS  testes automáticos (npm)     w=40.0     3.93s  8 passed
  FAIL  padrão do código             w=10.0     0.07s  7 fixable with --fix

  Resultado de ai-test-loop
  ----------------------------
  Nenhuma falha grave, mas a nota ficou abaixo do corte por causa dos avisos.

  Nota (parcial): 80 de 100 — precisa de 85 para passar.

  Não deu para checar (e por isso não conta como falha):
    - testes automáticos (Python): o projeto precisa de 'pytest' (pip install pytest)

SCORE=80.0
GATE=failed (round 1/1)
```

Quick mode obeys three rules, and they are why its score can be trusted:

- **It never invents a check.** A missing tool is reported as "could not check" — it never becomes
  a failure, and never becomes a pass either.
- **It never writes inside your project.** All state lives in `~/.cache/ai-test-loop/<hash>/`.
- **Nothing ran means no verdict.** If the environment is incomplete, you get exit code **2**
  ("could not judge") instead of a fake green. Exit codes: `0` passed, `1` failed, `2` inconclusive.

Real output, not a mockup:

```
  PASS  unit tests                   w=40     12.4s  41 passed in 12.31s
  FAIL  build                        w=20      3.1s  error TS2307: Cannot find module './api'
  PASS  no secrets                   w=15      0.8s  no leaks found
SCORE=75.0
GATE=failed (round 1/8)
GAPS=["build: exit code 2, expected 0"]
```

> **Want this wired into your repo, with the visual-diff gate and the delivery report?**
> The ready-made packs are at **https://skills.seu.pet/en/p/pack-completo?utm_source=npm&utm_medium=readme&utm_campaign=ai-test-loop**
> — or grab the free runner first and see if the loop earns its place.

No API keys, no dependencies, no model calls. The runner is deliberately dumb: exit codes and
optional output patterns decide pass/fail. The model never gets to *say* the work is good — it has
to *show* it.

---

## Install

Same package name everywhere. Both registries, same name:

```bash
npx ai-test-loop --help          # Node 18+ — nothing to install
pip install ai-test-loop         # Python 3.9+ — same engine (PyPI)
```

`npx` is the fastest path: it downloads and runs in about five seconds, no virtualenv, no Python.
The npm package carries a Node port of the runner; the PyPI package carries the Python one. Both
print the same `SCORE=` / `GAPS=` / `EVIDENCE=` contract, so the gate you run locally is the gate CI
runs.

Verify with a dry run — it lists the checks and weights without executing anything:

```bash
npx ai-test-loop --config templates/loop.example.json --dry-run
```

> Older docs and articles mention `pip install agent-test-loop`. That is the previous PyPI name and
> still works — it is the same engine, kept as a mirror so existing installs do not break. New code
> should use `ai-test-loop`.

## The problem

Ask an agent "is this done?" and it answers about its own reasoning. The same context that produced
the artifact also decides it is correct, so real defects survive to delivery:

- a handler that is never called (tests pass, feature dead)
- a test that passes because it asserts nothing
- a page that breaks only at mobile width
- a migration that works on the author's data and nowhere else
- a "fix" that reverted a config someone changed on purpose

Running "one more review pass" does not help, because the reviewer and the builder are the same
process. Separating them is the whole trick.

## Quickstart

```bash
npx ai-test-loop --config loop.json
```

Describe your checks once — name, command, weight:

```json
{
  "name": "my-project",
  "threshold": 85,
  "max_rounds": 8,
  "checks": [
    {"name": "unit tests", "cmd": "pytest -q", "weight": 35, "timeout_s": 900},
    {"name": "type check", "cmd": "mypy src", "weight": 10},
    {"name": "build",      "cmd": "npm run build", "weight": 20},
    {"name": "api up",     "cmd": "curl -sS 127.0.0.1:8000/health", "weight": 15,
     "must_match": "\"status\"\\s*:\\s*\"ok\""},
    {"name": "no secrets", "cmd": "gitleaks detect --no-banner", "weight": 20,
     "fail_if_output_matches": "leaks found: [1-9]", "severity": "block"}
  ]
}
```

A check that sets `required: true` without `severity` is **rejected at load**:
`required` alone never blocked anything (it only removed the check from the
"non-blocking" list), so the config author believed to have a wall and had a
discount. Declare `"block"` or `"score"` — the ambiguity is a config error.

`severity` decides what a failing check does:

- `"score"` (default) — the check costs its weight; the gate is the number.
- `"block"` — the check vetoes the run regardless of the total. A failing
  `block` check prints `GATE=blocked` and exits **3** (distinct from 1, "score
  too low"), so CI can tell "quality is short" from "wall". Use it for the
  checks where a high score must never buy a pass: secrets, SAST, auth.
  An absent `severity` keeps the previous behaviour, so existing configs are
  unaffected.

Output is machine-readable, so the *agent* can act on it and a CI job can gate on it:

```
SCORE=75.0
THRESHOLD=85
GATE=failed (round 1/8)
GAPS=["build: exit code 2, expected 0"]
EVIDENCE=loop-report-r1.json
```

Exit code is `0` when the gate passes and `1` when it fails — so `npx ai-test-loop --config
loop.json` is a valid CI step on its own, with no parsing.

Fix the **single largest** gap, run `--round 2`, repeat. Cap the rounds (8 is plenty): if the score
never converges, the check set is wrong, not the code.

`--round` cannot be used to buy extra attempts. The round number always advances
from the state file: `--round 1` on a run that already did three rounds becomes
round 4 and aborts with `exceeded max_rounds`. The flag can move the counter
forward, never back — an abort only happens once per run either way.

The first round fingerprints the config (`config_sha256`) and every later round
compares. Rewriting the config between rounds — deleting the check that failed,
lowering a weight, dropping the threshold — sets `config_drift: true` in the
report with `expected`/`actual` **and invalidates the gate** (`GATE=blocked`,
exit `3`). Detection alone was not enough: the config is written by the same
agent being graded, so recording the attack while keeping the verdict is
accepting the attack. Re-run with `--approve <approval_id>` to re-baseline; the
id and timestamp land in the state file as `approval_id`/`approval_ts`.
Changing only `name` is not drift; the fingerprint covers `threshold`,
`max_rounds`, `strict_tests` and the per-check `cmd`, `weight`, `severity`,
`required`, `expect_exit`, `must_match`, `fail_if_output_matches`, `timeout_s`,
`min_passed` and `allow_skipped`.

## Config reference

| Key | Meaning |
|---|---|
| `cmd` | (required) shell command, run with `bash -o pipefail -c` |
| `weight` | (required) points; weights are normalised, they need not sum to 100 |
| `timeout_s` | default 900 — a timeout is a **FAIL**, never a skip |
| `min_passed` | minimum tests reported as passed; an unrecognised summary is **NOT VERIFIABLE**, never a pass |
| `allow_skipped` | `false` fails the check on any skipped/xfail/todo count; omit to stay unpoliced |
| `strict_tests` | (top level) `true` sets `allow_skipped: false` on every check |
| `expect_exit` | default 0 |
| `must_match` | regex that must appear in the combined stdout+stderr |
| `fail_if_output_matches` | regex that fails the check when it appears |
| `required` | default `true`; `false` scores 0 but reports as a non-blocking gap |
| `cwd` | working directory for that check |

`bash -o pipefail` is not decoration: without it `pytest | tail` returns tail's exit code (0), so a
failing suite scores as passing. That is the most common false green in runners like this one.

## The four roles (never merge them)

| Role | Who | Job |
|---|---|---|
| Orchestrator | the agent running the task | picks checks, runs rounds, owns the report |
| Builder | the agent/model that wrote the artifact | fixes one class of gap per pass |
| Critic | this script + your checks | runs real commands, returns score + gaps |
| Reviewer | a **separate** context/model | audits critic and builder, can reopen the loop |

Rule zero: builder ≠ critic ≠ reviewer. If one context does all three, the loop is theatre.

## Three rules that make it honest

1. **Every point is earned by a command that actually ran.** No points for "read the code". The
   report stores the raw output next to each score.
2. **A skipped test is not a passing test.** `strict_tests: true` (or
   `allow_skipped: false` per check) rejects any `skipped`/`xfailed`/`todo`
   count and requires a recognisable test count; a summary the runner cannot
   parse becomes `NOT VERIFIABLE`, never a pass. Note that `pytest -q` with
   warnings enabled prints only `.s [100%]` and no summary line — the runner
   falls back to counting the progress characters.
3. **`NOT VERIFIABLE` is not a pass.** If a check cannot run (missing device, no network, jail), it
   scores 0 and the report says why. Silent skips are how a green board lies.
4. **The security gate is fail-closed.** A secret in the diff or history, a high-severity SAST
   finding, or an auth/path-traversal/injection issue found in review blocks delivery **even with a
   perfect score**. An inconclusive answer counts as blocked, never as pass.

## Reviewer panel

For anything other people will use, ask 2–3 specialists instead of one generalist — quality and
security always, performance only if there is a build or runtime cost, UX only if there is a
user-facing surface. Each returns strict JSON:

```json
{"parecer":"CONCORDO|DISCORDO","nota":0-100,"gaps":["..."],"texto":"one paragraph with evidence"}
```

Consensus → deliver. Majority disagree → reopen the loop with the combined gaps. Prompt in
`references/reviewer-prompt.md`, starting rubric in `references/scoring-rubric.md`.

## Telemetry (opt-out, and what it is not)

The `npx ai-test-loop` wrapper sends **one anonymous event per run** so the project knows whether
the tool is actually used — downloads alone are not usage (npm counts mirrors and CI).

What is sent: event type (`run`/`help`/`version`/`erro`), package version, Node major, OS name,
duration in ms, and — only on a real run — the final score and the number of gaps.

What is **never** sent: your IP (not logged server-side), e-mail, project path, file names, the
content of your checks, the command output, or anything from your code.

The install id is a random UUID written to `~/.ai-test-loop/telemetry-id` once. It counts machines,
not people. Delete that file and you are a new machine.

```bash
ai-test-loop --no-telemetry ...          # that run only
export AI_TEST_LOOP_NO_TELEMETRY=1       # permanently, in your shell profile
export DO_NOT_TRACK=1                    # the ecosystem-wide convention, also respected
# CI=1 is detected automatically: pipelines are not people
```

Telemetry never blocks and never waits: if the network is down the CLI runs exactly the same.

## When not to use it

Throwaway exploration, spike branches, and one-line answers where the cost of the loop exceeds the
cost of being wrong. It is for artifacts a human will rely on.

## Files

| Path | What |
|---|---|
| `bin/cli.js` | the `npx` entry point (wrapper + telemetry) |
| `scripts/loop_runner.js` | the deterministic critic, Node port — zero dependencies |
| `scripts/loop_runner.py` | the same critic in Python (used by the pip package) |
| `scripts/selftest.js` | 22 assertions covering every way a check can fail — `npm test` |
| `SKILL.md` | the loop, the scoring rules, the fail-closed gate — drop it in your agent's skills dir |
| `references/reviewer-prompt.md` | independent-review prompt, strict JSON output |
| `references/scoring-rubric.md` | starting weights |
| `templates/loop.example.json` | config with the usual check set |

`npm test` runs the selftest. It needs nothing but node.

## Get the full pack

This repo is the free, complete tool. The store has the ready-made packs for the cases where
writing the checks is the slow part — CI gates, visual regression, MCP servers, MLOps guards:

- Free download / e-mail delivery: **https://skills.seu.pet/gratis/ai-test-loop?utm_source=npm&utm_medium=readme&utm_campaign=ai-test-loop**
- Full pack: **https://skills.seu.pet/en/p/pack-completo?utm_source=npm&utm_medium=readme&utm_campaign=ai-test-loop**
- Catalog: **https://skills.seu.pet/?utm_source=npm&utm_medium=readme&utm_campaign=ai-test-loop**
- Want it installed and wired into your repo: **https://skills.seu.pet/servico?utm_source=npm&utm_medium=readme&utm_campaign=ai-test-loop**

## License

MIT — see `LICENSE`. Use it commercially, fork it, ship it.
