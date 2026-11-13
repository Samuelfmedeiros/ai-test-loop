"""Nucleo do gate do Roger: as funcoes PURAS que decidem bloqueio.

Extraido de scripts/loop_runner.py em 12/11/2026 para ser alvo viavel de mutation
testing. Medido: o runner tem 864 linhas e o mutmut expande o modulo para 22 MB —
compilar o arquivo mutado leva >180s, entao a rodada nunca terminava (3 tentativas,
30 min cada, mortas por timeout/restart).

Este modulo leva JUNTO as constantes do schema (TOP_LEVEL_KEYS, CHECK_KEYS,
FINGERPRINT_EXCLUDE, RUNNER_VERSION) e a excecao ConfigInvalida. A primeira
tentativa de extracao as deixou para tras e 21 dos 64 testes quebraram — funcao
que le constante do modulo de origem nao e pura, e extrair sem a constante e'
refactor pela metade.

O loop_runner.py IMPORTA daqui (nao redefine): tests/test_gate_core_paridade.py
falha se as duas verdades divergirem.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

class ConfigInvalida(Exception):
    """Config ambigua: nao da para dizer se o check bloqueia ou so pontua."""


def migrate_config(cfg: dict) -> tuple[dict, list[str]]:
    """Reescreve config da era pre-1.2 para o schema novo. Retorna (cfg, mudancas).

    `required: true` sem `severity` passou a ser ERRO DE CARGA em 12/11/2026, o
    que e correto (ambiguidade de gate e buraco) mas quebra todo usuario de
    pip/npx que atualizar: ele recebe "invalid config" e nao sabe o que fazer.
    Regressao silenciosa no canal onde o pacote se distribui nao e aceitavel —
    `--migrate-config` reescreve no arquivo e `--migrate-config --dry-run` mostra
    o diff sem tocar em nada.

    Regra da migracao: `required: true` sem severity vira `severity: "block"`,
    porque era ISSO que o autor tentou escrever (`required` era lido como
    "bloqueia"). `required: false`/ausente fica intacto.
    """
    mudancas = []
    for i, c in enumerate(cfg.get("checks", [])):
        if "severity" in c and c["severity"]:
            continue
        if c.get("required") is True:
            c["severity"] = "block"
            rotulo = c.get("name") or (c.get("cmd") or f"#{i}")
            mudancas.append(f"check {rotulo!r}: required=true -> severity=\"block\"")
    if cfg.get("strict_tests") is True:
        for i, c in enumerate(cfg.get("checks", [])):
            if "allow_skipped" not in c:
                c["allow_skipped"] = False
                rotulo = c.get("name") or (c.get("cmd") or f"#{i}")
                mudancas.append(
                    f"check {rotulo!r}: allow_skipped ausente -> false "
                    f"(strict_tests: true ja implicava isso)")
    return cfg, mudancas


def validate_config(cfg: dict) -> None:
    """Recusa config em que o gate pode ser burlado por omissao.

    `required: true` sem `severity` e o buraco medido em 12/11/2026: o autor do
    check acreditava ter criado um bloqueio e criou um desconto de peso. Quem
    escreve o config e o mesmo agente sendo avaliado — entao a ambiguidade e
    vetor, nao descuido. Falhar alto aqui e a unica leitura fail-closed.
    """
    problemas = []
    for c in cfg.get("checks", []):
        if c.get("required") and not c.get("severity"):
            problemas.append(
                f"check {c.get('name') or c.get('cmd', '')[:40]!r}: required=true sem "
                f"severity — declare severity: \"block\" (bloqueia) ou \"score\" (so pontua)")
    if problemas:
        raise ConfigInvalida("; ".join(problemas))


# Schema da config: a UNICA fonte de verdade do que entra no fingerprint.
# Medido 12/11/2026: a versao anterior usava uma lista literal por check, entao
# `strict_tests`/`min_passed`/`allow_skipped` (que chegaram depois) ficaram de
# fora do hash e o agente desligava o anti-cheat sem disparar drift. Lista
# literal sempre atrasa o proximo adendo — o hash passa a derivar do schema, e
# tests/test_fingerprint_schema.py falha se uma chave do config ficar de fora.
TOP_LEVEL_KEYS = ("name", "threshold", "max_rounds", "strict_tests",
                  "blocking_gaps", "version", "runner")
CHECK_KEYS = ("name", "cmd", "cwd", "weight", "severity", "required", "expect_exit",
              "must_match", "fail_if_output_matches", "timeout_s",
              "min_passed", "allow_skipped", "junit_xml", "erosion", "min_coverage")
# Chaves que NAO entram no hash por serem rotulo/telemetria, nao regua.
FINGERPRINT_EXCLUDE = frozenset({"name", "version", "runner", "comment", "notes"})


RUNNER_VERSION = "1.2.0"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def drift_approval_id(drift: dict) -> str:
    """ID de aprovacao DERIVADO do drift — o agente nao inventa o proprio bilhete.

    Medido 12/11/2026: `--approve id-que-eu-inventei` re-baseava o config e
    liberava o gate (exit 0). O id passa a ser funcao do drift concreto
    (expected+actual), entao aprovar exige TER o drift em maos e ele so existe
    no relatorio gerado pelo proprio runner.
    """
    canon = json.dumps({"e": drift.get("expected"), "a": drift.get("actual")},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:8]


def runner_fingerprint() -> dict:
    """Identidade do PROPRIO critic: o runner e o JS irmao.

    Medido 12/11/2026 (furo estrutural): congelar a config nao adianta nada se o
    agente pode reescrever o codigo que LE a config. `score()` adulterado para
    `return 100.0` aprova qualquer entrega e nenhum hash de config percebe.
    O orquestrador (que vive FORA deste repo) mantem o hash esperado e compara.
    """
    aqui = os.path.dirname(os.path.abspath(__file__))
    js = os.path.join(aqui, "loop_runner.js")
    return {
        "version": RUNNER_VERSION,
        "py_sha256": _sha256_file(os.path.abspath(__file__)),
        "js_sha256": _sha256_file(js) if os.path.exists(js) else None,
    }


def config_fingerprint(cfg: dict) -> str:
    """Hash estavel da REGUA, derivado do SCHEMA (nao de lista literal).

    Cobre toda chave conhecida top-level e por check — inclusive as que forem
    adicionadas no futuro: ou entram em TOP_LEVEL_KEYS/CHECK_KEYS, ou o teste
    do schema quebra e obriga a decisao explicita. Sem isso um adendo novo
    (anti-cheat, cobertura) fica fora do hash e o drift nao dispara.
    """
    def _norm(v):
        # Ausente e False precisam colidir: `allow_skipped: false` escrito na mao
        # e o default omitido sao a MESMA regua, nao um drift.
        return v

    canon = {}
    for k in TOP_LEVEL_KEYS:
        if k in FINGERPRINT_EXCLUDE:
            continue
        if k not in cfg:
            continue
        v = cfg.get(k)
        if k == "strict_tests":
            v = bool(v)
        canon[k] = _norm(v)
    canon["checks"] = []
    for c in cfg.get("checks", []):
        item = {}
        for k in CHECK_KEYS:
            if k in FINGERPRINT_EXCLUDE or k not in c:
                continue
            v = c.get(k)
            if k == "allow_skipped":
                v = bool(v)
            item[k] = _norm(v)
        canon["checks"].append(item)
    payload = json.dumps(canon, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def score(checks: list[dict]) -> float:
    total = sum(c["weight"] for c in checks) or 1.0
    earned = sum(c["weight"] for c in checks if c["passed"])
    return round(earned / total * 100, 1)


def parse_junit_xml(path: str) -> dict:
    """Conta testes a partir de JUnit XML — a fonte AUTORITATIVA.

    Medido 12/11/2026: o parser por linha de progresso (`-v` imprime nome de
    cada teste, nao `.s`) quebra conforme a flag que o agente escolher. JUnit
    XML e escrito pelo proprio pytest/jest e nao muda com `-q`/`-v`, entao
    quando existe e ele quem manda. Formato ilegivel = {} (NOT VERIFIABLE).
    """
    try:
        import xml.etree.ElementTree as ET
        raiz = ET.parse(path).getroot()
    except Exception:
        return {}
    if raiz.tag not in ("testsuites", "testsuite"):
        return {}
    total = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    casos = raiz.iter("testcase")
    vistos = False
    for caso in casos:
        vistos = True
        if caso.find("failure") is not None or caso.find("error") is not None:
            total["failed"] += 1
        elif caso.find("skipped") is not None:
            total["skipped"] += 1
        else:
            total["passed"] += 1
    return total if vistos else {}


def parse_test_counts(output: str, junit_path: str | None = None) -> dict:
    """Conta testes que REALMENTE rodaram.

    Ordem de confianca (medido 12/11/2026):
      1. JUnit XML — escrito pelo framework, independe de `-q`/`-v`;
      2. sumarios textuais (pytest/jest/node:test);
      3. linha de progresso `.sxXFE` — SO como fallback informativo, porque
         `-v` imprime nome de teste em vez de pontos e o parser fica cego.
    Devolve {} quando nada e reconhecido — quem exige numero trata como
    NOT VERIFIABLE, nunca como aprovado.
    """
    if junit_path and os.path.exists(junit_path):
        do_xml = parse_junit_xml(junit_path)
        if do_xml:
            return do_xml

    found: dict = {}

    def _bump(key: str, n: int) -> None:
        found[key] = found.get(key, 0) + n

    # jest / vitest: "Tests:       2 skipped, 10 passed, 12 total"
    # Parseado PRIMEIRO e a linha e' removida antes do regex generico: sem isso o
    # mesmo numero era contado duas vezes (medido 12/11/2026 — "2 skipped, 10 passed,
    # 12 total" virava skipped=4/passed=21). O efeito nao era cosmetico: com
    # `passed` inflado, `min_passed` passava com MENOS testes reais que o exigido.
    linhas_restantes = []
    for linha in output.splitlines():
        m_jest = re.match(r"^\s*Tests:\s*(.+)$", linha)
        if not m_jest:
            linhas_restantes.append(linha)
            continue
        for parte in m_jest.group(1).split(","):
            hit = re.match(r"\s*(\d+)\s+(\w+)", parte)
            if hit:
                _bump(hit.group(2), int(hit.group(1)))
    output = "\n".join(linhas_restantes)

    # pytest: "5 passed, 2 skipped, 1 xfailed in 0.42s"
    # Duas armadilhas medidas em 12/11/2026, nesta ordem:
    #  (a) regex com \b varria a saida inteira e casava "Test Suites: 1 passed" do
    #      jest — suite contada como teste (10 testes viravam 11);
    #  (b) ancorar em `^` e parar na primeira ocorrencia perdia as demais
    #      categorias da MESMA linha ("5 passed, 2 failed" contava so 5).
    # Solucao: quebrar a linha de sumario por virgula e somar cada par. Ancorado em
    # inicio de linha para nao casar "Test Suites:"/"Time:".
    for linha in output.splitlines():
        if not re.search(r"\d+\s+(passed|failed|skipped|xfailed|xpassed|errors?|error)\b",
                         linha):
            continue
        if re.match(r"^\s*(Test Suites|Time|Snapshots|Ran all)\b", linha):
            continue
        for parte in linha.split(","):
            hit = re.match(r"\s*(\d+)\s+(passed|failed|skipped|xfailed|xpassed|errors?|error)\b",
                           parte)
            if hit:
                key = {"errors": "error", "error": "error"}.get(hit.group(2), hit.group(2))
                _bump(key, int(hit.group(1)))

    # node:test TAP: "# pass 10" / "# skip 2" / "# todo 0"
    for m in re.finditer(r"^#\s*(pass|fail|skip|todo|cancelled)\s+(\d+)\s*$", output, re.M):
        _bump(m.group(1), int(m.group(2)))

    if found:
        return found

    # Fallback: linha de progresso do pytest -q. Medido 12/11/2026 — com warnings
    # na sessao o pytest suprime o sumario ("1 passed, 1 skipped") e imprime so
    # `.s   [100%]`. Ler apenas o sumario deixava o check CEGO e ele passava
    # verde com teste pulado. Aqui conta-se o progresso caractere a caractere.
    progresso = {".": "passed", "s": "skipped", "x": "xfailed", "X": "xpassed",
                 "F": "failed", "E": "error"}
    for linha in output.splitlines():
        alvo = linha.strip()
        if not re.match(r"^[.sxXFE]+\s*(\[\s*\d+%\]|$)", alvo):
            continue
        simbolos = re.match(r"^([.sxXFE]+)", alvo).group(1)
        for ch in simbolos:
            _bump(progresso[ch], 1)
    return found
