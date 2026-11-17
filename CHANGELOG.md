# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versionamento: [SemVer](https://semver.org/lang/pt-BR/).

## [1.2.7] — 2026-11-17

### Corrigido

- **README em uma língua só.** O arquivo tinha a documentação em inglês e, depois de
  `## License`, uma segunda documentação em português no mesmo arquivo — quem lia em
  inglês caía em seções em português no meio da referência técnica. Agora `README.md`
  é inglês, `README.pt-BR.md` é português, com link entre os dois no topo.
- O exemplo de saída no README em inglês ganhou a nota de que o CLI imprime em pt-BR
  (é saída real, não tradução).

## [1.2.6] — 2026-11-17

### Corrigido

- **Sem argumento agora roda no projeto, em vez de imprimir a ajuda.** Medido no
  `orders.db` de produção: 48 instalações, 33 pediram `--help` e pararam, 26 delas com
  um único evento — viram a ajuda uma vez e nunca voltaram. Se o diretório atual tem
  marcador de projeto (`package.json`, `pyproject.toml`, `go.mod`, …), o CLI roda direto
  nele; senão mostra a ajuda como antes.
- **Convite do rodapé apontava para o produto grátis.** Ele mandava para
  `/p/ai-test-loop`, a mesma página que o usuário acabou de rodar, onde a oferta é
  "baixar grátis" — o que já estava no disco. Agora aponta para o pack completo, que é
  o próximo degrau real de quem rodou o motor.

## [1.2.3] — 2026-11-14

### Adicionado

- **Uma linha no fim do relatório apontando o pack.** Medido: 1284 downloads npm por
  semana e 28 cliques na loja em 30 dias, ou seja 2,2% dos que instalam visitam o site.
  Quem roda `npx` não abre README, então nunca descobre a versão paga. O convite imprime
  uma linha depois da nota, com UTM `cli/rodape`.

### Corrigido

- **Relatório legível morria com `KeyError: 'segredos'` no modo `--config`.** Nesse
  caminho o runner passa `achados={}` e as chaves da varredura de segurança não existem.
  Rodava todos os checks, calculava a nota e estourava antes de imprimir qualquer coisa.
  Agora usa `.get` e o relatório sai completo.

### Silêncio garantido

O convite não aparece em `CI`, `CONTINUOUS_INTEGRATION`, `BUILD_NUMBER`,
`GITHUB_ACTIONS`, `GITLAB_CI`, `JENKINS_URL`, `NO_COLOR` ou `ATL_NO_CTA=1`. Propaganda em
log de pipeline quebra contrato. Testado nas duas linguagens (Python e Node).

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
  --approve-by Roger` re-baseia com snapshot.
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

### Publicacao PyPI (14/11/2026)

- `scripts/publica-py.sh`: builda e publica o pacote CANONICO `ai-test-loop`.
  Antes nao existia script nem pyproject canonico commitado (a 1.1.3 foi publicada
  com o pyproject trocado na mao e restaurado depois), entao o repo nao
  reproduzia o que estava no PyPI.
- O script sincroniza os runners embutidos em `ai_test_loop/scripts/` e verifica
  a versao antes de subir.
- Corrigido no caminho: `gate_core.py` faltava no pacote e o comando instalado
  morria com `ModuleNotFoundError: No module named 'gate_core'` — bug que so
  apareceria para quem instalasse do PyPI.
