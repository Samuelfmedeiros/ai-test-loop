# ai-test-loop

**Your agent is grading its own homework.** This makes it impossible: a critic script runs real
checks, scores the artifact 0–100, loops until the threshold is met, and a fail-closed gate blocks
delivery on security findings.

![loop](https://github.com/Samuelfmedeiros/ai-test-loop/actions/workflows/loop.yml/badge.svg)

No API keys, no dependencies, no model calls. `loop_runner.py` is deliberately dumb: exit codes and
optional output patterns decide pass/fail. The model never gets to *say* the work is good — it has
to *show* it.

> **Quer isso pronto para o seu time?** Os packs com entrega por e-mail, o gate com relatório
> e o guia de verificação estão em **https://skills.seu.pet/gratis/ai-test-loop**

---

## Install as a package

```bash
pip install agent-test-loop
agent-test-loop --config loop.json --dry-run
```

PyPI: https://pypi.org/project/agent-test-loop/

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
git clone https://github.com/Samuelfmedeiros/ai-test-loop
cd ai-test-loop

# 1. describe your checks (name, command, weight — weights sum to 100)
cat > loop.json <<'JSON'
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
     "fail_if_output_matches": "leaks found: [1-9]", "required": true}
  ]
}
JSON

# 2. run the critic
python3 scripts/loop_runner.py --config loop.json
```

Output is machine-readable, so the *agent* can act on it and a CI job can gate on it:

```
SCORE=72
GAPS=["build: command exited 1", "api up: output did not match pattern"]
EVIDENCE=.loop/report-1.json
```

Fix the **single largest** gap, run `--round 2`, repeat. Cap the rounds (8 is plenty): if the score
never converges, the check set is wrong, not the code.

A self-contained demo lives in `examples/demo` and runs in CI on every push, so you can see a real
pass and a real fail without configuring anything.

## The four roles (never merge them)

| Role | Who | Job |
|---|---|---|
| Orchestrator | the agent running the task | picks checks, runs rounds, owns the report |
| Builder | the agent/model that wrote the artifact | fixes one class of gap per pass |
| Critic | this script + your checks | runs real commands, returns score + gaps |
| Reviewer | a **separate** context/model | audits critic and builder, can reopen the loop |

Rule zero: builder ≠ critic ≠ reviewer. If one context does all three, the loop is theatre.

## Three rules that make it honest

1. **Every point is earned by a command that actually ran.** No points for "read the code". Weights
   sum to 100 and the report stores the raw output next to each score.
2. **`NOT VERIFIABLE` is not a pass.** If a check cannot run (missing device, no network, jail), it
   scores 0 and the report says why. Silent skips are how a green board lies.
3. **The security gate is fail-closed.** A secret in the diff or history, a high-severity SAST
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

## When not to use it

Throwaway exploration, spike branches, and one-line answers where the cost of the loop exceeds the
cost of being wrong. It is for artifacts a human will rely on.

## Files

| Path | What |
|---|---|
| `SKILL.md` | the loop, the scoring rules, the fail-closed gate — drop it in your agent's skills dir |
| `scripts/loop_runner.py` | the deterministic critic (stdlib only) |
| `references/reviewer-prompt.md` | independent-review prompt, strict JSON output |
| `references/scoring-rubric.md` | starting weights |
| `templates/loop.example.json` | config with the usual check set |
| `examples/demo/` | self-contained example used by CI |

## Get the full pack

This repo is the free, complete tool. The store has the ready-made packs for the cases where
writing the checks is the slow part — CI gates, visual regression, MCP servers, MLOps guards:

- Free download / e-mail delivery: **https://skills.seu.pet/gratis/ai-test-loop**
- Catalog: **https://skills.seu.pet**
- Want it installed and wired into your repo: **https://skills.seu.pet/servico**

## License

MIT — see `LICENSE`. Use it commercially, fork it, ship it.

---

### Em português

O loop de teste para agentes: o construtor não assina o próprio trabalho. Um crítico roda checagens
de verdade, dá nota 0–100, repete até o limite e barra a entrega se houver achado de segurança.
`python3 scripts/loop_runner.py --config loop.json` — sem chave de API e sem dependência.

Versão com entrega por e-mail e os packs prontos: https://skills.seu.pet/gratis/ai-test-loop
