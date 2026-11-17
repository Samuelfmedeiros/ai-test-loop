# ai-test-loop (português)

*[English documentation](README.md)*

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
`--approve-by Roger` é obrigatório. O state guarda `approval_snapshot` com o
drift e a config do momento — sem ele auditar depois é impossível.

```bash
# o bloqueio imprime o id correto:
GATE=blocked — motivo=config_drift: regua alterada sem aprovacao (a1b2 -> c3d4);
reexecute com --approve 7f3a91cc --approve-by Roger
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
