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
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone

DEFAULT_TIMEOUT = 900
MAX_OUTPUT_CHARS = 8000
_SHELL: list[str] | None = None


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
    cfg.setdefault("max_rounds", 8)
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

    passed = not reasons
    evidence = combined.strip()
    if len(evidence) > MAX_OUTPUT_CHARS:
        evidence = evidence[: MAX_OUTPUT_CHARS // 2] + "\n...[truncated]...\n" + evidence[-MAX_OUTPUT_CHARS // 2:]
    return {
        "name": name,
        "cmd": chk["cmd"],
        "weight": float(chk["weight"]),
        "required": bool(chk.get("required", True)),
        "passed": passed,
        "reasons": reasons,
        "exit_code": rc,
        "duration_s": duration,
        "evidence": evidence,
    }


def score(checks: list[dict]) -> float:
    total = sum(c["weight"] for c in checks) or 1.0
    earned = sum(c["weight"] for c in checks if c["passed"])
    return round(earned / total * 100, 1)


def main() -> int:
    ap = argparse.ArgumentParser(
        add_help=True,
        description="Deterministic critic for the AI Test Loop",
        # 09/11/2026: o --help era o UNICO ponto de contato do dev com o pacote e
        # nao dizia nada sobre o resto do produto. Medido: 555 downloads/npm no mes
        # e ZERO hits com utm_source=npm na loja. O epilog e o unico lugar honesto
        # de por o link (nao interrompe a saida, nao quebra script de terceiro).
        epilog=("Full version, packs and paid support: "
                "https://skills.seu.pet/p/ai-test-loop"
                "?utm_source=cli&utm_medium=help&utm_campaign=ai-test-loop"),
    )
    ap.add_argument("--config", help="arquivo loop.json (opcional se usar --repo)")
    ap.add_argument("--repo", help="caminho da pasta ou URL do repositório — monta o teste sozinho")
    ap.add_argument("--round", type=int)
    ap.add_argument("--json", action="store_true", help="also dump the full report as JSON to stdout")
    ap.add_argument("--quiet", action="store_true", help="only SCORE/GAPS/EVIDENCE lines")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show-state", action="store_true")
    args = ap.parse_args()

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

    round_no = args.round
    if round_no is None:
        prev = 0
        if os.path.exists(state_path):
            with open(state_path, "r", encoding="utf-8") as fh:
                prev = len(json.load(fh).get("rounds", []))
        round_no = prev + 1

    if round_no > int(cfg["max_rounds"]):
        print(f"ROUND: {round_no}  ABORT: exceeded max_rounds={cfg['max_rounds']}")
        print("GAPS=[\"round budget exhausted — the check set or the threshold needs revisiting\"]")
        return 1

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
    gate = value >= threshold
    gaps = [f"{c['name']}: {'; '.join(c['reasons'])}" for c in results if not c["passed"] and c["required"]]
    soft = [f"{c['name']}: {'; '.join(c['reasons'])}" for c in results if not c["passed"] and not c["required"]]

    report = {
        "project": cfg["name"],
        "round": round_no,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "threshold": threshold,
        "score": value,
        "gate": "passed" if gate else "failed",
        "checks": results,
        "gaps": gaps,
        "non_blocking_gaps": soft,
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
                            "checks": results})
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
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
