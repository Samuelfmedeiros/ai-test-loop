# ai-test-loop

**Your agent grades its own homework.** This makes that impossible: a critic script runs real checks,
scores the artifact 0–100, and a fail-closed gate blocks delivery below the threshold.

```bash
npx ai-test-loop --config loop.json     # zero install, no Python needed
```

**Just want a score for your project?** Paste the path or the GitHub URL — no config file:

```bash
npx ai-test-loop --repo https://github.com/you/your-project
```

Real output from a project that had no config at all:

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

---

### Em português

O loop de teste para agentes: o construtor não assina o próprio trabalho. Um crítico roda checagens
de verdade, dá nota 0–100 e barra a entrega abaixo do limite.

```bash
npx ai-test-loop --config loop.json   # não precisa de Python
```

Sem chave de API, sem dependência, sem chamada de modelo. Versão com entrega por e-mail e os packs
prontos: https://skills.seu.pet/gratis/ai-test-loop?utm_source=npm&utm_medium=readme&utm_campaign=ai-test-loop

## Integridade do runner e do contrato (v1.1.2, 12/11/2026)

O gate era decorativo porque **quem escreve o config é quem é avaliado**. Quatro
furos medidos por exploit e fechados nesta versão:

### 1. O crítico não pode ser editável pelo réu

`config_sha256` congela a régua, não o código que lê a régua. O agente pode
reescrever `score()` para `return 100.0` e o hash de config não percebe.
Agora o runner publica `runner: {version, py_sha256, js_sha256}` no relatório e
comparar com o hash esperado é papel do orquestrador, que vive **fora** do repo:

```bash
python3 ~/.hermes/scripts/roger-runner-pin.py pin --by "<quem>"
python3 ~/.hermes/scripts/roger-runner-pin.py check      # exit 3 se divergir
ROGER_RUNNER_PY_SHA256=$(python3 ~/.hermes/scripts/roger-runner-pin.py env --key py) \
  ./loop_runner.py --config loop.json
```

Hash divergente → `GATE=blocked`, exit 3, `motivo=runner_drift`. O caso legítimo
exige `--approve-runner --approve-runner-reason "<por quê>"` — aceitar sem motivo
não é aceitar.

### 2. O fingerprint deriva do schema

Lista literal de campos atrasa o próximo adendo: `strict_tests`, `min_passed` e
`allow_skipped` chegaram depois e ficaram **fora** do hash. O hash agora deriva de
`TOP_LEVEL_KEYS`/`CHECK_KEYS`, e `tests/test_fingerprint_schema.py` falha quando o
loader conhece uma chave que o schema não cobre. Foi esse teste que encontrou
`cwd` fora do hash.

### 3. `--approve` não é auto-serviço

O ID de aprovação é **derivado do drift** (`sha256(expected+actual)[:8]`) e
`--approve-by <nome>` é obrigatório. O state guarda `approval_snapshot` com o
drift e a config do momento — sem ele auditar depois é impossível.

```bash
# o bloqueio imprime o id correto:
GATE=blocked — motivo=config_drift: regua alterada sem aprovacao (a1b2 -> c3d4);
reexecute com --approve 7f3a91cc --approve-by <nome>
```

### 4. `blocking_gaps` bloqueia (exit 3), não desconta

Lista de regex case-insensitive no topo do config. Gap que casa invalida a
entrega mesmo com score acima do threshold. Regex inválido bloqueia
(fail-closed): padrão mal escrito não vira porta aberta.

### Contagem de testes: JUnit XML manda

`junit_xml: "<arquivo>"` no check torna a contagem autoritativa — escrita pelo
framework, independe de `-q`/`-v`. O parser por linha de progresso (`.sxXFE`)
continua como fallback informativo, porque `-v` imprime nome de teste e cega o
parser. Saída mentirosa ("5 passed") perde para o XML real.

---

# MIGRAÇÃO 1.1.x → 1.2.0 (breaking para configs antigos)

**O que mudou:** `required: true` sem `severity` deixou de ser aceito. Antes o
check era "bloqueante ou pontuável" a depender de quem lia o config — ambiguidade
que o próprio agente avaliado podia explorar. Agora é **erro de carga**.

**Quem é afetado:** qualquer config com `required: true` e sem `severity`. Se a
sua instalação atualiza por `pip` ou `npx` e não tinha `severity`, a próxima
execução termina com:

```
[loop_runner] invalid config: check 'nome': required=true sem severity
[loop_runner] para corrigir automaticamente: loop_runner.py --config <arquivo> --migrate-config
```

**Como migrar (um comando, com backup automático):**

```bash
# 1. ver o que mudaria, sem tocar em nada
loop_runner.py --config seu-loop.json --migrate-config --dry-run

# 2. aplicar (grava seu-loop.json.bak-pre-migrate antes)
loop_runner.py --config seu-loop.json --migrate-config

# Node/npx
npx ai-test-loop --config seu-loop.json --migrate-config
```

A regra da migração assume a intenção original: `required: true` significava
"quero isso bloqueando", então vira `severity: "block"`. Se a intenção era só
pontuar, troque para `severity: "score"` depois — o importante é que a decisão
seja explícita em vez de depender do leitor.

`strict_tests: true` também completa `allow_skipped: false` nos checks que não
declaram o campo (era o efeito implícito até a 1.1.2).

**Idempotente:** rodar de novo imprime "nada a migrar".

---

# Mapa de severidade por classe de falha (v1.2.0)

Gap que vira desconto de ponto é gap que ninguém corrige. A varredura dos **62
critics** extraiu **257 gaps literais** de `gaps.append(...)` e classificou cada
um por classe de falha. Padrão que não casa nenhum gap real é teatro e foi
removido na validação.

**Bloqueiam (exit 3):** segredo · seguranca · antifraude · testes · build ·
cobertura · deploy · dados · ui · generico

**Não bloqueiam:** estilo (reversível em segundos) · infra (tem watchdog próprio;
bloquear aqui duplica o alarme no canal errado) · ambiente (`NÃO VERIFICÁVEL` é
ausência de prova, não prova de falha)

## Precedência

O primeiro padrão que casar decide. Sem ordem, `test/front-audit.mjs ausente —
sem medição no navegador` casaria `testes` (bloqueia) e `ambiente` (aviso), e o
gap escaparia do bloqueio.

```
1. segredo      2. seguranca    3. antifraude   4. testes
5. build        6. cobertura    7. deploy       8. dados
9. ui          10. generico    11. estilo     12. infra   13. ambiente
```

## Resultado medido

Antes: 3 critics bloqueavam, 59 descontavam ponto. Gap de credencial vazada em
arquivo versionado valia −5 num rubric de 100.

Depois: 199 padrões bloqueantes nas 53 configs; 257/257 gaps classificados,
0 órfãos. Verificação contra o orquestrador real — 12/12 casos no veredito
esperado (8 bloqueiam, 4 passam).

Mapa versionado em `~/.hermes/profiles/ajudante/state/mapa-severidade-62-critics.json`;
`tests/test_severidade_mapa.py` falha se uma classe bloqueante ficar sem gap real
(regex morto = trava decorativa).

## Mutation testing do gate (v1.2.0)

**Score medido: 78,1%** (353 de 452 mutantes mortos, piso 70% no CI).

O alvo é `scripts/gate_core.py` — as funções que **decidem bloqueio**
(`score`, os dois fingerprints, `drift_approval_id`, `migrate_config`,
`validate_config`, `parse_junit_xml`, `parse_test_counts`). Elas foram extraídas do
`loop_runner.py` em 12/11/2026 por um motivo medido: o runner tem 864 linhas e o
mutmut expande o módulo inline para 21 MB — compilar o arquivo mutado levava mais de
180s e a rodada de 1827 mutantes nunca terminava.

Por que isso importa mais que o meta-gate: o meta-gate do §10 prova que **um caso**
passa. Mutation testing prova que a suíte **pega regressão**.

### Rodar localmente

```bash
pip install mutmut pytest
mutmut run                    # ~20s, 452 mutantes
mutmut results                # lista os sobreviventes
mutmut export-cicd-stats      # killed/survived/total em JSON
```

No CI (`.github/workflows/loop.yml`, job `mutation`, `needs: critic`) o piso é 70%,
e o job também falha se nenhum mutante for gerado — alvo removido é medição
ausente, não aprovação.

### Três armadilhas que custaram rodadas

1. **Teste que chama o runner por `subprocess` não mata mutante.** 9 dos 10 arquivos
   de teste executam `loop_runner.py` como processo; dentro de `mutants/` o runner
   não acha a config do mutmut, sai com erro de ambiente e o mutante morre por
   crash. Todos parecem mortos e o score mede o sandbox, não os testes. Só
   `pytest_add_cli_args_test_selection` com os arquivos que **importam** o módulo.
2. **Import tem de ser qualificado** (`from scripts.gate_core import ...`). Com
   `import gate_core` bare, as trampolines saem como `gate_core.x_...` contra
   `scripts.gate_core.x_...` esperadas e o mutmut aborta.
3. **`do_not_mutate = ["mutants/*"]` é obrigatório** — sem ele o mutmut copia a
   própria árvore mutada dentro de si e a geração não termina.

### Dois bugs que a mutação revelou

Os mutantes sobreviventes em `parse_test_counts` não eram só falta de cobertura:

- `Tests: 2 skipped, 10 passed, 12 total` (jest/vitest) era contado **duas vezes**
  — pelo parser específico e pelo regex genérico — virando `passed: 21`;
- `Test Suites: 1 passed` era somado como teste, inflando `passed` de 10 para 11.

Nos dois casos o efeito era o mesmo: `min_passed` passava com **menos testes reais**
do que o exigido. Corrigido, com regressão em `tests/test_gate_parsing.py` (39 testes).
