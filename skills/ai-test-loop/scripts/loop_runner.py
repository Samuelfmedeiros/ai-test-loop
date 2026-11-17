#!/usr/bin/env python3
"""loop_runner — the deterministic critic of the AI Test Loop.

Runs a list of weighed checks for real, captures their raw output as evidence, and prints:

    SCORE=<0-100>
    GAPS=["<check>: <why it failed>", ...]
    EVIDENCE=<path to the JSON report>

The agent (builder) is not allowed to grade its own work; this script measures. It is
deliberately dumb: exit codes + optional output patterns decide pass/fail. No model calls,
no network, no telemetry.

Config (JSON):

{
  "name": "my-project",
  "threshold": 85,
  "max_rounds": 8,
  "checks": [
    {"name": "unit tests",   "cmd": "pytest -q",           "weight": 40, "timeout_s": 900},
    {"name": "build",        "cmd": "npm run build",       "weight": 25, "timeout_s": 600},
    {"name": "lint",         "cmd": "ruff check .",        "weight": 10},
    {"name": "no secrets",   "cmd": "gitleaks detect --no-banner", "weight": 15,
     "fail_if_output_matches": "leaks found: [1-9]"},
    {"name": "api responds", "cmd": "curl -sS localhost:8000/health", "weight": 10,
     "must_match": "\"status\"\\s*:\\s*\"ok\""}
  ]
}

Check keys:
  cmd                      (required) shell command; runs with pipefail semantics via bash -o pipefail -c
  weight                   (required) points; all weights are normalised, so they need not sum to 100
  timeout_s                (optional, default 900) hard timeout — a timeout is a FAIL, never a skip
  expect_exit              (optional, default 0) exit code that means pass
  must_match               (optional) regex that MUST appear in combined output
  fail_if_output_matches   (optional) regex that fails the check when it appears
  min_passed               (optional) minimum tests that must report as PASSED; an
                           unrecognised summary is NOT VERIFIABLE, never a pass
  allow_skipped            (optional, default false) when false, any skipped/xfail/
                           todo count fails the check — a skipped test is not a pass
  required                 (optional, default true) when false, a FAIL scores 0 but is reported as a
                           non-blocking gap (useful for "nice to have" checks)
  cwd                      (optional) working directory for this check

Usage:
    python loop_runner.py --config loop.json [--round 2] [--json] [--quiet]
    python loop_runner.py --config loop.json --show-state      # print round history and stop
    python loop_runner.py --config loop.json --dry-run         # list checks, run nothing

Exit code: 0 when SCORE >= threshold (gate passed), 1 when below, 2 on usage/config error.

State: <config dir>/.<config-stem>-state.json accumulates every round, so a crash or a restart
never loses the evidence trail. The report of the latest round is written to
<config dir>/<config-stem>-report-r<round>.json. Names are derived from the config file so two
projects in the same folder never share a history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone

# O nucleo do gate vive em gate_core.py — alvo pequeno e mutavel (mutation testing).
# Re-exportado aqui para preservar a API publica: `import loop_runner; loop_runner.score`
# continua funcionando. tests/test_gate_core_paridade.py falha se as duas verdades
# divergirem (funcao redefinida aqui em vez de importada).
# Import qualificado (scripts.gate_core): o mutmut 3 casa as trampolines pelo
# caminho do modulo, e `import gate_core` bare produz chaves `gate_core.x_...`
# contra `scripts.gate_core.x_...` esperadas — ele aborta com "tests import the
# source under a different module path". O fallback cobre `python scripts/loop_runner.py`,
# onde sys.path[0] e' o proprio scripts/.
try:
    from scripts.gate_core import (  # noqa: E402
    CHECK_KEYS,
    FINGERPRINT_EXCLUDE,
    RUNNER_VERSION,
    TOP_LEVEL_KEYS,
    ConfigInvalida,
    _sha256_file,
    config_fingerprint,
    drift_approval_id,
    migrate_config,
    parse_junit_xml,
    parse_test_counts,
    runner_fingerprint,
    score,
    validate_config,
    )
except ImportError:  # execucao direta: python scripts/loop_runner.py
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from gate_core import (  # noqa: E402
        CHECK_KEYS,
        FINGERPRINT_EXCLUDE,
        RUNNER_VERSION,
        TOP_LEVEL_KEYS,
        ConfigInvalida,
        _sha256_file,
        config_fingerprint,
        drift_approval_id,
        migrate_config,
        parse_junit_xml,
        parse_test_counts,
        runner_fingerprint,
        score,
        validate_config,
    )

DEFAULT_TIMEOUT = 900
MAX_OUTPUT_CHARS = 8000
# Onde a evidencia INTEGRAL de cada check e' gravada. Vazio = nao grava (compat).
# O orquestrador aponta para uma pasta fora do repo avaliado quando quer prova
# que o agente nao pode reescrever; sem isso, fica ao lado do report.
EVIDENCE_DIR = os.environ.get("ROGER_EVIDENCE_DIR", "")
_SHELL: list[str] | None = None


def tree_binding(path: str) -> dict:
    """Prova a QUE arvore o veredito pertence (furo 1.4, 15/11/2026).

    Sem isso o relatorio diz `gate: passed` e nada amarra esse veredito ao codigo
    que foi medido: o agente commita, faz checkout, e reapresenta o JSON antigo
    como prova do estado novo. Medido: `diff_sha256`/`rev-parse HEAD` nao existiam
    no motor — o relatorio era um passaporte sem foto.

    Tudo aqui e' OPCIONAL e best-effort: fora de um repo Git, devolve o que deu.
    Ausencia de campo NUNCA vira "limpo" — vira `git: false`.
    """
    info = {"git": False, "head": None, "branch": None, "dirty": None,
            "dirty_files": None, "diff_sha256": None, "diff_lines": None}

    def _git(*a):
        try:
            p = subprocess.run(("git",) + a, cwd=path, capture_output=True,
                               text=True, timeout=30, stdin=subprocess.DEVNULL)
            return p.returncode, p.stdout
        except (OSError, subprocess.SubprocessError):
            return 1, ""

    rc, out = _git("rev-parse", "HEAD")
    if rc != 0:
        return info
    info["git"] = True
    info["head"] = out.strip()
    rc, out = _git("rev-parse", "--abbrev-ref", "HEAD")
    info["branch"] = out.strip() if rc == 0 else None
    # `diff HEAD` cobre staged+unstaged de arquivos rastreados; o hash e' do
    # CONTEUDO do diff, nao do path — dois checkouts diferentes com a mesma
    # alteracao dao o mesmo hash, que e' o que queremos comparar.
    rc, out = _git("diff", "HEAD")
    if rc == 0:
        info["diff_sha256"] = hashlib.sha256(out.encode("utf-8", "replace")).hexdigest()
        info["diff_lines"] = len(out.splitlines())
        rc_u, untracked = _git("ls-files", "--others", "--exclude-standard")
        files = [l for l in out.splitlines() if l.startswith("diff --git")]
        if rc_u == 0:
            # Os artefatos do PROPRIO gate (loop.json, *-report-rN.json,
            # .*-state.json) nascem untracked e fariam toda arvore parecer suja
            # — medido 15/11: repo recem-criado dava dirty=True files=1 sem
            # nenhuma alteracao real. Sujeira tem que significar codigo mudado.
            _ruido = re.compile(r"(^|/)(\.?[^/]*-state\.json|[^/]*-report-r\d+\.json|"
                                r"loop\.json|\.roger-evidence/)")
            files += [f"untracked: {l}" for l in untracked.splitlines()
                      if l.strip() and not _ruido.match(l)]
        info["dirty_files"] = len(files)
        info["dirty"] = bool(files)
    return info


def load_config(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"[loop_runner] config not found: {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"[loop_runner] config is not valid JSON: {exc}")
    checks = cfg.get("checks")
    if not isinstance(checks, list) or not checks:
        sys.exit("[loop_runner] config needs a non-empty 'checks' list")
    for i, chk in enumerate(checks):
        if not chk.get("cmd"):
            sys.exit(f"[loop_runner] check #{i} has no 'cmd'")
        if not isinstance(chk.get("weight", None), (int, float)) or chk["weight"] <= 0:
            sys.exit(f"[loop_runner] check {chk.get('name', i)!r} needs a positive 'weight'")
    cfg.setdefault("name", os.path.basename(os.path.dirname(os.path.abspath(path))) or "loop")
    cfg.setdefault("threshold", 85)
    # 13/11/2026 — `setdefault` NAO substitui chave existente com valor nulo:
    # `"threshold": null` passava o default e estourava em `float(None)` na hora
    # do gate, com traceback em vez de erro de config legivel. Medido por
    # exploit: exit 1 + TypeError, sem dizer qual chave. Normaliza aqui.
    if cfg.get("threshold") is None:
        cfg["threshold"] = 85
    if cfg.get("max_rounds") is None:
        cfg["max_rounds"] = 8
    cfg.setdefault("max_rounds", 8)
    # Ambiguidade de gate e erro de config, nao aviso: `required: true` sem
    # `severity` deixa o check bloqueante ou so pontuavel a depender de quem le.
    try:
        validate_config(cfg)
    except ConfigInvalida as exc:
        sys.exit(f"[loop_runner] invalid config: {exc}\n"
                 f"[loop_runner] para corrigir automaticamente: "
                 f"loop_runner.py --config <arquivo> --migrate-config")
    return cfg


def _shell() -> list[str]:
    """Shell wrapper for one check, decided once per process.

    bash -o pipefail is the strong default: without it `pytest | tail` returns tail's
    exit code (0) and a failing check scores as passing — the most common false green.

    But hardcoding "bash" crashes on machines that have no bash (minimal containers,
    alpine, stripped PATH). Measured: FileNotFoundError traceback, not a gate failure.
    So: bash when available, /bin/sh on POSIX (pipefail lost but the check runs), and
    cmd.exe on Windows (where bash only exists if Git Bash is installed).
    """
    global _SHELL
    if _SHELL is not None:
        return _SHELL
    def works(cmd: str) -> bool:
        try:
            return subprocess.run([cmd, "-c", "exit 0"], capture_output=True).returncode == 0
        except (FileNotFoundError, PermissionError, OSError):
            return False
    if os.name == "nt":
        _SHELL = ["bash", "-o", "pipefail", "-c"] if works("bash") else [
            os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c"]
    else:
        _SHELL = ["bash", "-o", "pipefail", "-c"] if works("bash") else ["/bin/sh", "-c"]
    return _SHELL






def run_check(chk: dict) -> dict:
    """Run one check for real, return its result record with raw evidence."""
    name = chk.get("name") or chk["cmd"][:40]
    timeout = int(chk.get("timeout_s", DEFAULT_TIMEOUT))
    cwd = chk.get("cwd") or None
    expect_exit = int(chk.get("expect_exit", 0))
    # bash -o pipefail: sem ele `pytest | tail` devolve o exit code do tail (0) e o check
    # que falhou pontua como aprovado — o falso verde mais comum deste tipo de runner.
    wrapped = _shell() + [chk["cmd"]]
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            wrapped, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        rc = None
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    duration = round(time.time() - started, 2)
    combined = (out or "") + ("\n" + err if err else "")

    reasons: list[str] = []
    if timed_out:
        reasons.append(f"timed out after {timeout}s")
    elif rc != expect_exit:
        reasons.append(f"exit code {rc}, expected {expect_exit}")

    must = chk.get("must_match")
    if must and not re.search(must, combined):
        reasons.append(f"output does not match required pattern /{must}/")

    fail_pat = chk.get("fail_if_output_matches")
    if fail_pat:
        hit = re.search(fail_pat, combined)
        if hit:
            reasons.append(f"output matched failure pattern /{fail_pat}/: {hit.group(0)!r}")

    # Anti-cheat: teste que nao roda nao e teste aprovado. `min_passed` exige
    # numero real de casos passando, e `allow_skipped: false` (default) recusa
    # qualquer skip/xfail. Sem isso o agente marca @pytest.mark.skip no caso que
    # falha (ou deixa a suite coletar zero) e o board fica verde. Medido 12/11/2026.
    counts = {}
    min_passed = chk.get("min_passed")
    # Opt-in de proposito: sem `min_passed` e sem `allow_skipped` explicito o check
    # nao e policiado, para nao reprovar suite que ja tinha skip legitimo com a
    # config antiga. `strict_tests: true` no config liga a policia em todos.
    politica = chk.get("allow_skipped")
    # O censo do anti-cheat (furo 1.8) precisa da contagem SEMPRE, mesmo em check
    # que não declara política: é o número que vira o teto da próxima rodada.
    _jx = chk.get("junit_xml")
    _jpath = None
    if _jx:
        _jpath = _jx if os.path.isabs(_jx) else os.path.join(cwd or os.getcwd(), _jx)
    counts = parse_test_counts(combined, junit_path=_jpath)
    if min_passed is not None:
        if not counts:
            reasons.append(
                f"NOT VERIFIABLE: min_passed={min_passed} exigido mas nenhum "
                f"sumario de testes reconhecido na saida (pytest/jest/vitest/node:test)")
        elif counts.get("passed", 0) < int(min_passed):
            reasons.append(
                f"only {counts.get('passed', 0)} passing tests, min_passed={min_passed}")
    if politica is False and not counts and not reasons:
        reasons.append(
            "NOT VERIFIABLE: allow_skipped=false exige contagem de testes e nenhum "
            "sumario nem linha de progresso foi reconhecida na saida")
    if counts and politica is False:
        pulados = {k: v for k, v in counts.items()
                   if k in ("skipped", "xfailed", "todo") and v}
        if pulados:
            detalhe = ", ".join(f"{v} {k}" for k, v in pulados.items())
            reasons.append(
                f"{detalhe} — skip/xfail nao conta como aprovado (allow_skipped: false)")

    passed = not reasons
    evidence = combined.strip()
    # Furo 1.7 (medido 15/11/2026): o corte era metade da CABECA + metade do RABO —
    # justamente o miolo onde mora a assertion que falhou era o unico trecho
    # descartado. Um relatorio que perde a linha do erro "prova" que tudo passou.
    # Agora: cabeca + janela em volta do PRIMEIRO erro + rabo, e o texto integral
    # vai para disco com sha256 no registro (hash curto = prova de nao-edicao).
    full = combined
    evidence_sha256 = hashlib.sha256(full.encode("utf-8", "replace")).hexdigest()
    evidence_lines = full.count("\n") + 1 if full else 0
    evidence_path = None
    if EVIDENCE_DIR:
        try:
            os.makedirs(EVIDENCE_DIR, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)[:60].strip("-") or "check"
            evidence_path = os.path.join(EVIDENCE_DIR, f"{safe}.log")
            with open(evidence_path, "w", encoding="utf-8", errors="replace") as fh:
                fh.write(full)
        except OSError as exc:  # nomear o que quebrou, nunca engolir
            evidence_path = f"(nao gravavel: {type(exc).__name__}: {exc})"
    if len(evidence) > MAX_OUTPUT_CHARS:
        half = MAX_OUTPUT_CHARS // 2
        _erro = re.compile(
            r"(Failed|FAILED|AssertionError|Error|Traceback|error:|FAIL\b|\[E\]|"
            r"EOENT|expected .* got|assert)",
            re.IGNORECASE)
        _linhas = evidence.splitlines()
        _alvo = 0
        for _i, _l in enumerate(_linhas):
            if _erro.search(_l):
                _alvo = _i
                break
        _ini = max(0, _alvo - 20)
        _fim = min(len(_linhas), _alvo + 20)
        _janela = "\n".join(_linhas[_ini:_fim])
        evidence = (
            evidence[:half]
            + f"\n...[truncated: {len(_linhas)} linhas no total, "
              f"janela em volta da 1a falha (linha {_alvo + 1}) abaixo]...\n"
            + _janela
            + f"\n...[truncated]...\n"
            + evidence[-half:]
        )
    return {
        "name": name,
        "cmd": chk["cmd"],
        "weight": float(chk["weight"]),
        "required": bool(chk.get("required", True)),
        # severity: "block" falha DURA (exit 3), independente de peso/score.
        # Ausente = comportamento anterior (score puro) — compatibilidade total.
        "severity": str(chk.get("severity", "score")).strip().lower(),
        "passed": passed,
        "reasons": reasons,
        "exit_code": rc,
        "duration_s": duration,
        "evidence": evidence,
        # Integridade da evidencia (furo 1.7): o texto acima e' recortado para
        # caber no relatorio; ESTES campos provam o que foi de fato executado.
        # `evidence_sha256` cobre a saida INTEGRAL, nao o recorte.
        "evidence_sha256": evidence_sha256,
        "evidence_lines": evidence_lines,
        "evidence_path": evidence_path,
        "evidence_truncated": len(full) > MAX_OUTPUT_CHARS,
        # Censo (furo 1.8): quantos testes EXISTEM/PASSAM nesta rodada. Sem isso
        # gravado no report, a proxima rodada nao tem teto para comparar e apagar
        # testes que falham vira o caminho mais barato para subir o score.
        "counts": counts or {},
    }








# Schema da config: a UNICA fonte de verdade do que entra no fingerprint.
# Medido 12/11/2026: a versao anterior usava uma lista literal por check, entao
# `strict_tests`/`min_passed`/`allow_skipped` (que chegaram depois) ficaram de
# fora do hash e o agente desligava o anti-cheat sem disparar drift. Lista
# literal sempre atrasa o proximo adendo — o hash passa a derivar do schema, e
# tests/test_fingerprint_schema.py falha se uma chave do config ficar de fora.
# Chaves que NAO entram no hash por serem rotulo/telemetria, nao regua.














def main() -> int:
    ap = argparse.ArgumentParser(
        add_help=True,
        description="Deterministic critic for the AI Test Loop",
        # 09/11/2026: o --help era o UNICO ponto de contato do dev com o pacote e
        # nao dizia nada sobre o resto do produto. Medido: 555 downloads/npm no mes
        # e ZERO hits com utm_source=npm na loja. O epilog e o unico lugar honesto
        # de por o link (nao interrompe a saida, nao quebra script de terceiro).
        # 15/11/2026: o destino era /p/ai-test-loop, a pagina do produto GRATIS que
        # o usuario ja tem. Quem pede --help ja instalou o pacote — o degrau dele
        # e o pack. Medido: 1.839 downloads npm/30d e 0 venda.
        epilog=("Full version, packs and paid support: "
                "https://skills.seu.pet/p/pack-completo"
                "?utm_source=cli&utm_medium=help&utm_campaign=ai-test-loop"),
    )
    ap.add_argument("--config", help="arquivo loop.json (opcional se usar --repo)")
    ap.add_argument("--repo", help="caminho da pasta ou URL do repositório — monta o teste sozinho")
    ap.add_argument("--round", type=int)
    ap.add_argument("--json", action="store_true", help="also dump the full report as JSON to stdout")
    ap.add_argument("--quiet", action="store_true", help="only SCORE/GAPS/EVIDENCE lines")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show-state", action="store_true")
    ap.add_argument("--approve", metavar="ID",
                    help="registra aprovacao do config alterado; o ID tem de ser o "
                         "derivado do drift (sha256 do drift_detail, 8 chars) — id "
                         "inventado e recusado. Sem ele, config_drift=true INVALIDA o gate")
    ap.add_argument("--approve-by", metavar="NOME", default=None,
                    help="quem aprova (humano/pipeline). Obrigatorio quando o proprio "
                         "agente avalia: aprovacao anonima nao e auditoria")
    ap.add_argument("--migrate-config", action="store_true",
                    help="reescreve o config da era pre-1.2 (required:true sem severity "
                         "-> severity:\"block\") e sai. Use com --dry-run para ver o diff")
    ap.add_argument("--approve-runner", action="store_true",
                    help="aceita runner adulterado (exige --approve-runner-reason)")
    ap.add_argument("--approve-runner-reason", metavar="TEXTO", default=None)
    args = ap.parse_args()

    # Migracao do schema (1.2.0): `required: true` sem `severity` virou erro de
    # carga. Quem atualiza por pip/npx e recebe "invalid config" precisa de uma
    # saida dentro do proprio pacote — apontar o comando no erro nao basta se o
    # comando nao existe.
    if args.migrate_config:
        if not args.config:
            sys.exit("[loop_runner] --migrate-config exige --config <arquivo>")
        alvo = pathlib.Path(args.config)
        if not alvo.exists():
            sys.exit(f"[loop_runner] config not found: {args.config}")
        try:
            dados = json.loads(alvo.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            sys.exit(f"[loop_runner] config is not valid JSON: {exc}")
        dados, mudancas = migrate_config(dados)
        if not mudancas:
            print("[migrate] nada a migrar — config ja esta no schema 1.2")
            return 0
        for m in mudancas:
            print(f"[migrate] {m}")
        if args.dry_run:
            print(f"[migrate] DRY-RUN: {len(mudancas)} mudanca(s) NAO gravadas "
                  f"({args.config})")
            return 0
        backup = alvo.with_suffix(alvo.suffix + ".bak-pre-migrate")
        backup.write_text(alvo.read_text(encoding="utf-8"), encoding="utf-8")
        tmp = alvo.with_suffix(alvo.suffix + ".tmp")
        tmp.write_text(json.dumps(dados, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, alvo)
        print(f"[migrate] {len(mudancas)} mudanca(s) gravadas em {args.config} "
              f"(backup: {backup.name})")
        return 0

    # `--config` que nao existe nao pode matar a execucao (medido 13/11/2026 no
    # telemetria: 51 erros contra 23 runs, e a maioria morria em 3ms com
    # "config not found"). Quem digita `--config loop.json` seguindo a doc antiga
    # esta no diretorio do projeto e QUER testar; o arquivo e detalhe de schema,
    # nao pre-requisito. Cai no auto-probe do diretorio atual e avisa.
    if args.config and not os.path.exists(args.config) and not args.repo:
        sys.stderr.write(
            f"[loop_runner] {args.config} nao existe — montando o teste do "
            f"diretório atual ({os.getcwd()}). Crie o loop.json depois, se quiser "
            f"fixar seus proprios comandos.\n")
        args.repo = os.getcwd()
        args.config = None

    # Modo rápido (11/11/2026): sem `--config` e com `--repo`, o teste se monta sozinho.
    # Quem tem a config escrita continua igual — este caminho só existe para não
    # obrigar ninguém a aprender o schema antes de ver o primeiro resultado.
    faltando: list[dict] = []
    achados: dict = {}
    if args.repo and not args.config:
        from repo_probe import build_config, cache_dir, prepare_repo
        raiz = prepare_repo(args.repo)
        cfg_auto, faltando, achados = build_config(raiz, args.repo)
        cache = cache_dir(os.path.abspath(raiz))
        destino = os.path.join(cache, "loop.json")
        pathlib.Path(destino).write_text(
            json.dumps(cfg_auto, indent=2, ensure_ascii=False), encoding="utf-8")
        args.config = destino
        # O modo rápido é uma foto do projeto AGORA, não uma campanha de rodadas:
        # reaproveitar o estado anterior faria a segunda execução abortar com
        # "max_rounds exhausted" (medido 11/11/2026). Quem quer histórico usa
        # --config, onde o estado é do usuário e continua intocado.
        for velho in (".loop-state.json", "loop-report-r1.json"):
            alvo = os.path.join(cache, velho)
            if os.path.exists(alvo):
                os.remove(alvo)
        os.environ["ATL_REPO_ROOT"] = raiz
        if not cfg_auto["checks"]:
            # nada executável detectado: não inventa check vazio nem dá nota falsa
            print("Nenhum teste automático encontrado neste projeto.")
            for f in faltando:
                print(f"  não deu para checar {f['nome']}: {f['motivo']}")
            print("Rode com --config para apontar seus próprios comandos.")
            return 2
    elif not args.config:
        sys.stderr.write("ai-test-loop: informe --repo <pasta|url> ou --config <loop.json>\n")
        return 2

    cfg_path = os.path.abspath(args.config)
    cfg = load_config(cfg_path)
    base = os.path.dirname(cfg_path)
    # Per-config filenames: two configs in the same folder must not share one state file,
    # otherwise rounds from project A pollute project B's history and round numbers.
    stem = os.path.splitext(os.path.basename(cfg_path))[0]
    state_path = os.path.join(base, f".{stem}-state.json")

    if args.show_state:
        if not os.path.exists(state_path):
            print("[loop_runner] no state file yet")
            return 0
        with open(state_path, "r", encoding="utf-8") as fh:
            st = json.load(fh)
        for r in st.get("rounds", []):
            print(f"round {r['round']}: SCORE={r['score']} threshold={r['threshold']} "
                  f"failed={[c['name'] for c in r['checks'] if not c['passed']]}")
        return 0

    if args.dry_run:
        for c in cfg["checks"]:
            print(f"[dry-run] {c.get('name', c['cmd'][:40])}  weight={c['weight']}  cmd={c['cmd']}")
        print(f"[dry-run] threshold={cfg['threshold']} max_rounds={cfg['max_rounds']}")
        return 0

    # Orcamento de tentativas tem que ser impossivel de burlar: antes
    # `round_no = args.round` sem validar monotonicidade — o agente chamava
    # `--round 1` dez vezes e `round_no > max_rounds` nunca disparava (state
    # acumulava dez entradas de round 1). Medido 12/11/2026: 5 chamadas com
    # --round 1, teto 3, nenhuma abortou. Agora o round SEMPRE avanca: o valor
    # do state manda e --round so pode EMPURRAR para frente, nunca voltar.
    prev = 0
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as fh:
                prev = len(json.load(fh).get("rounds", []))
        except (json.JSONDecodeError, OSError):
            prev = 0
    round_overridden = False
    if args.round is None:
        round_no = prev + 1
    else:
        round_no = max(prev + 1, int(args.round))
        round_overridden = round_no != int(args.round)

    if round_no > int(cfg["max_rounds"]):
        print(f"ROUND: {round_no}  ABORT: exceeded max_rounds={cfg['max_rounds']}")
        print("GAPS=[\"round budget exhausted — the check set or the threshold needs revisiting\"]")
        return 1

    # `strict_tests: true` no config liga a policia anti-cheat em TODOS os checks
    # (allow_skipped=false). Sem isso, so os checks que declaram a politica sao
    # policiados — config antiga nao muda de comportamento.
    if cfg.get("strict_tests"):
        for _c in cfg["checks"]:
            if _c.get("allow_skipped") is None:
                _c["allow_skipped"] = False

    results = []
    for chk in cfg["checks"]:
        if not args.quiet:
            print(f"[loop_runner] round {round_no}: running {chk.get('name', chk['cmd'][:40])!r} ...",
                  file=sys.stderr, flush=True)
        results.append(run_check(chk))

    # Modo rápido: falta de dependência do projeto não é falha do projeto. Só neste
    # caminho — com --config o usuário declarou o que quer medir e a falha vale.
    if args.repo:
        try:
            from repo_probe import reclassify
            results, faltando_dep = reclassify(results)
            faltando = faltando + faltando_dep
            if not results:
                print("Não deu para verificar nada: faltam dependências deste projeto.")
                for f in faltando:
                    print(f"  - {f['nome']}: {f['motivo']}")
                return 2
        except ImportError as exc:
            sys.stderr.write(f"[aviso] reclassificação indisponível: {exc}\n")

    value = score(results)
    threshold = float(cfg["threshold"])
    # Régua congelada: o round 1 grava a impressao digital da config; todo round
    # seguinte compara. Sem isso o agente apaga o check que falhou, baixa o peso
    # ou derruba o threshold entre rounds e o relatorio nao registra nada —
    # comparar dois rounds vira comparar réguas diferentes. Medido 12/11/2026.
    _prev_state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as _fh:
                _prev_state = json.load(_fh)
        except (json.JSONDecodeError, OSError):
            _prev_state = {}
    _fp = config_fingerprint(cfg)
    _drift = None
    if _prev_state.get("config_sha256") and _prev_state["config_sha256"] != _fp:
        _drift = {"expected": _prev_state["config_sha256"], "actual": _fp}
    # Bloqueio duro: check severity="block" que falhou impede entrega mesmo com
    # score acima do threshold. Medido 12/11/2026: antes um check required=false
    # ou de peso baixo (segurança, peso 5) falhava e o gate dava passed/exit 0.
    blocking = [c for c in results if not c["passed"] and c.get("severity") == "block"]
    # `blocking_gaps`: gaps que INVALIDAM a entrega em vez de so descontar peso.
    # Medido 12/11/2026: "sem testes novos nesta rodada" valia -10 pontos no
    # critic e o gate dava passed. Regex invalido bloqueia (fail-closed): um
    # padrao mal escrito nao pode virar porta aberta.
    gaps = [f"{c['name']}: {'; '.join(c['reasons'])}" for c in results if not c["passed"] and c["required"]]
    soft = [f"{c['name']}: {'; '.join(c['reasons'])}" for c in results if not c["passed"] and not c["required"]]
    _gap_block = []
    _padroes = cfg.get("blocking_gaps") or []
    if _padroes:
        try:
            _rx = [re.compile(p, re.IGNORECASE) for p in _padroes]
        except re.error as exc:
            _gap_block.append(f"blocking_gaps invalido ({exc}) — config recusada")
            _rx = []
        for _g in list(gaps) + list(soft):
            for _p in _rx:
                if _p.search(_g):
                    _gap_block.append(_g)
                    break
    gate = value >= threshold and not blocking and not _gap_block
    # Drift sem aprovacao e gate invalido, nao "aviso no relatorio": quem escreve
    # o config e o agente avaliado, entao registrar o ataque e deixar o veredito
    # de pe e aceitar o ataque. Aprovar exige --approve <approval_id> e fica no
    # state, para a mudanca ser rastreavel na Fase 3 do contrato.
    # Aprovacao do drift: o ID tem de ser o DERIVADO do drift (sha256 8 chars) e
    # exige --approve-by. Medido 12/11/2026: `--approve id-que-eu-inventei`
    # re-baseava o config e o gate dava passed/exit 0 — aprovacao auto-servico.
    _approve_esperado = drift_approval_id(_drift) if _drift else None
    _approve_ok = bool(_drift) and args.approve == _approve_esperado and bool(args.approve_by)
    drift_invalida = bool(_drift) and not _approve_ok
    if _drift and args.approve and not _approve_ok:
        if args.approve != _approve_esperado:
            drift_invalida_motivo = (f"approval_id invalido: esperado "
                                     f"{_approve_esperado!r} (derivado do drift), "
                                     f"recebido {args.approve!r}")
        else:
            drift_invalida_motivo = "--approve-by obrigatorio (aprovacao anonima nao audita)"
    else:
        drift_invalida_motivo = None

    # Integridade do RUNNER: o agente escreve no mesmo repo do critic, entao
    # congelar a config sem congelar o codigo que a le nao protege nada.
    # O hash esperado vem do orquestrador (fora do repo) via env.
    _runner_fp = runner_fingerprint()
    _esperado_py = os.environ.get("ROGER_RUNNER_PY_SHA256")
    runner_drift = bool(_esperado_py) and _esperado_py != _runner_fp["py_sha256"]
    if runner_drift and args.approve_runner and not args.approve_runner_reason:
        runner_drift = True  # aceitar sem motivo nao e aceitar
    elif runner_drift and args.approve_runner:
        runner_drift = False

    # ── Anti-cheat de CENSO (furo 1.8, 15/11/2026) ─────────────────────────
    # O gate media "quantos testes passaram", nunca "quantos testes EXISTEM".
    # Assim, apagar os testes que falham sobe o score: 158 verdes viram 120
    # verdes e nada reclama. `min_passed` no config e' um chao ESTATICO — se o
    # agente edita o config ele edita o chao (a config tem hash, mas o hash
    # congela o valor novo, nao denuncia a queda).
    #
    # Aqui o censo e' DERIVADO: o maior `passed` ja visto para o mesmo check na
    # mesma config vira o teto, e a rodada atual nao pode ficar abaixo de
    # `erosion_max_pct` (default 20%) dele. Erosao legitima (teste removido de
    # proposito) tem caminho explicito: `allow_test_removal: "motivo"` no check.
    _censo = {}
    _erosao = []
    _pct = float(cfg.get("erosion_max_pct", 20))
    for _r in _prev_state.get("rounds", []):
        for _reg in (_r.get("checks") or []):
            _n, _p = _reg.get("name"), (_reg.get("counts") or {}).get("passed")
            if _n and isinstance(_p, int) and _p > 0:
                _censo[_n] = max(_censo.get(_n, 0), _p)
    for _reg in results:
        _n = _reg.get("name")
        _p = (_reg.get("counts") or {}).get("passed")
        _antes = _censo.get(_n)
        if _antes and isinstance(_p, int):
            _queda = _antes - _p
            if _queda > 0 and _queda > int(_antes * _pct / 100.0):
                _motivo = None
                for _orig in cfg.get("checks", []):
                    if (_orig.get("name") or "") == _n:
                        _motivo = _orig.get("allow_test_removal")
                if not _motivo:
                    _msg = (f"erosao de testes em '{_n}': {_p} passando agora vs "
                            f"{_antes} no maior round anterior (-{_queda}, "
                            f"limite {_pct:.0f}%) — teste removido/desativado nao "
                            f"e' teste aprovado. Declare allow_test_removal no check "
                            f"se a remocao for intencional")
                    _erosao.append(_msg)
                    _reg.setdefault("reasons", []).append(_msg)
                    _reg["passed"] = False

    _tree_raiz = cfg.get("repo") or os.getcwd()
    _tree = tree_binding(_tree_raiz)

    if _erosao:
        # O score foi calculado ANTES da erosão; sem recalcular, o check que caiu
        # continuaria pontuando como aprovado no número final e o bloqueio seria
        # só cosmético no relatório. Medido no teste: score não mudava.
        value = score(results)
        gaps.extend(_erosao)

    report = {
        "project": cfg["name"],
        "round": round_no,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "threshold": threshold,
        "blocking": [c["name"] for c in blocking],
        "blocked": bool(blocking),
        "score": value,
        "gate": "passed" if (gate and not drift_invalida and not runner_drift) else "failed",
        "checks": results,
        "gaps": gaps,
        "non_blocking_gaps": soft,
        "blocking_gaps_hit": _gap_block,
        "config_sha256": _fp,
        "config_drift": bool(_drift),
        "config_drift_detail": _drift,
        "drift_invalidates_gate": drift_invalida,
        "drift_invalida_motivo": drift_invalida_motivo,
        "runner": _runner_fp,
        "runner_drift": runner_drift,
        # Furo 1.4: a QUE arvore este veredito pertence. Sem isso o JSON antigo
        # serve de prova para codigo novo (commit/checkout depois do gate).
        "tree": _tree,
        "approved_by": args.approve if drift_invalida is False and _drift else None,
        "round_overridden": round_overridden,
    }
    report_path = os.path.join(base, f"{stem}-report-r{round_no}.json")
    pathlib.Path(report_path).write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    state = {"project": cfg["name"], "rounds": []}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
            state.setdefault("rounds", [])
        except json.JSONDecodeError:
            pass
    state["rounds"].append({"round": round_no, "score": value, "threshold": threshold,
                            "gate": report["gate"], "ts": report["ts"], "report": report_path,
                            "checks": results, "config_sha256": _fp,
                            "config_drift": bool(_drift),
                            "round_overridden": round_overridden})
    if _approve_ok:
        # Aprovar uma mudanca de regua RE-BASEIA o fingerprint: senao todo round
        # seguinte continua marcando drift da config antiga. O snapshot do drift
        # aprovado vai junto — sem ele, auditar depois e impossivel: o state
        # guardava approval_id/ts mas nao O QUE foi aprovado (medido 12/11/2026).
        state["config_sha256"] = _fp
        state["approval_id"] = args.approve
        state["approval_by"] = args.approve_by
        state["approval_ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["approval_snapshot"] = {
            "drift": _drift,
            "approved_sha256": _fp,
            "previous_sha256": (_drift or {}).get("expected"),
            "config_at_approval": {
                "threshold": cfg.get("threshold"),
                "max_rounds": cfg.get("max_rounds"),
                "strict_tests": bool(cfg.get("strict_tests", False)),
                "blocking_gaps": cfg.get("blocking_gaps"),
                "checks": [{k: c.get(k) for k in ("name", "weight", "severity",
                                                  "allow_skipped", "min_passed")}
                           for c in cfg.get("checks", [])],
            },
        }
    else:
        state.setdefault("config_sha256", _fp)
    pathlib.Path(state_path).write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    if not args.quiet:
        for c in results:
            status = "PASS" if c["passed"] else "FAIL"
            detail = c["evidence"].strip().splitlines()[-1][:160] if c["evidence"].strip() else "(no output)"
            print(f"  {status}  {c['name']:<28} w={c['weight']:<5} {c['duration_s']:>7}s  {detail}")

    if args.repo and not args.quiet:
        # Saída em português de gente ANTES do bloco técnico. Quem colou o projeto
        # para "ver se está bom" não é necessariamente programador; o SCORE/GAPS
        # continua abaixo porque CI e script de terceiro dependem dele.
        try:
            from repo_probe import human_report
            print(human_report(os.environ.get("ATL_REPO_ROOT", base), cfg, faltando,
                               achados, value, gate, results))
        except Exception as exc:  # nunca esconder a nota por causa do resumo
            sys.stderr.write(f"[aviso] resumo legível falhou: {type(exc).__name__}: {exc}\n")

        # Uma linha, depois do relatório e antes do bloco técnico. Cala em CI.
        try:
            from repo_probe import convite
            linha = convite(cfg)
            if linha:
                print(linha)
        except Exception as exc:
            sys.stderr.write(f"[aviso] convite falhou: {type(exc).__name__}: {exc}\n")

    # Se nada com peso cheio rodou, não existe veredito — e anunciar GATE=passed aqui
    # faria um script de CI tratar "0 verificado" como aprovado. Estado próprio, com
    # exit 2 (erro de uso/ambiente), nunca 0.
    inconclusivo = False
    if args.repo:
        try:
            from repo_probe import inconclusive
            inconclusivo = inconclusive(results)
        except ImportError:
            inconclusivo = False

    print(f"SCORE={value}")
    print(f"THRESHOLD={threshold}")
    if inconclusivo:
        print("GATE=inconclusive (nada com peso cheio chegou a rodar — veja o que falta instalar)")
    elif runner_drift:
        print(f"GATE=blocked (round {round_no}/{cfg['max_rounds']}) — motivo="
              f"runner_drift: o critic foi alterado no repo (py "
              f"{_runner_fp['py_sha256'][:12]} != esperado {(_esperado_py or '')[:12]}); "
              f"corrija o runner ou use --approve-runner --approve-runner-reason \"<motivo>\"")
    elif drift_invalida:
        print(f"GATE=blocked (round {round_no}/{cfg['max_rounds']}) — motivo="
              f"config_drift: regua alterada sem aprovacao "
              f"({_drift['expected'][:12]} -> {_drift['actual'][:12]}); "
              f"reexecute com --approve {_approve_esperado} --approve-by Roger"
              + (f" [{drift_invalida_motivo}]" if drift_invalida_motivo else ""))
    elif _gap_block:
        print(f"GATE=blocked (round {round_no}/{cfg['max_rounds']}) — motivo="
              f"blocking_gap: {len(_gap_block)} gap(s) bloqueante(s): "
              f"{'; '.join(_gap_block)[:300]}")
    elif blocking:
        print(f"GATE=blocked (round {round_no}/{cfg['max_rounds']}) — "
              f"check(s) severity=block falharam: {', '.join(c['name'] for c in blocking)}")
    else:
        print(f"GATE={'passed' if gate else 'failed'} (round {round_no}/{cfg['max_rounds']})")
    print(f"GAPS={json.dumps(gaps, ensure_ascii=False)}")
    if soft:
        print(f"NON_BLOCKING_GAPS={json.dumps(soft, ensure_ascii=False)}")
    print(f"EVIDENCE={report_path}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if inconclusivo:
        return 2
    # Exit 3 = bloqueio duro (severity=block falhou). Distinto de 1 (nota baixa)
    # para o CI separar "qualidade insuficiente" de "parede".
    if blocking or drift_invalida or runner_drift or _gap_block:
        return 3
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
