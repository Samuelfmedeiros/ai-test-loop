# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versionamento: [SemVer](https://semver.org/lang/pt-BR/).

## [1.2.1] — 2026-11-13

### Corrigido

- **Chave de configuração com valor `null` aprovava tudo no runner JS.**
  `setdefault("threshold", 85)` (Python) e `if (cfg.threshold === undefined)` (JS)
  só cobrem chave **ausente**, não chave presente com valor nulo.
  - Python: `float(None)` → `TypeError` com traceback, exit 1, sem dizer qual chave.
  - JS: `Number(null) === 0`, então `score >= 0` era sempre verdadeiro e **o gate
    aprovava qualquer pontuação, inclusive zero**.

  Agora `threshold` e `max_rounds` nulos normalizam para o default nos dois runners.

### Adicionado

- `tests/test_config_null.py`: 3 contratos × 2 runners = 6 testes (sem estouro,
  check falhando reprova, `max_rounds` nulo usa default).

## [1.2.0] — 2026-10-16

### Adicionado

- `severity: "block"` por check: check bloqueante que falha → `GATE=blocked` e
  **exit 3**, distinto de `failed` (exit 1). `required: true` sem `severity` é
  recusado na carga da config.
- `config_fingerprint()`: SHA-256 de `threshold`, `max_rounds` e dos campos de
  cada check. Drift entre rodadas → `GATE=blocked`; `--approve <id>
  --approve-by <nome>` re-baseia com snapshot.
- `runner_fingerprint()` (`{version, py_sha256, js_sha256}`) + `roger-runner-pin.py`
  para detectar runner alterado entre rodadas.
- Orçamento de rodadas monotônico: `round_no = max(prev+1, args.round)` — `--round 1`
  repetido não estende mais o limite.
- Anti-cheat: `strict_tests`, `allow_skipped`, `min_passed`, `junit_xml`
  autoritativo, `blocking_gaps`. Skip/xfail não conta como aprovado.
- Parser com fallback por caractere de progresso (`. s x X F E`) para o caso em
  que `pytest -q` não imprime sumário.
- Mutation testing do gate.

## [1.0.3]

### Corrigido

- Falso limpo em falha de scanner.
