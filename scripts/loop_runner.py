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


def run_check(chk: dict) -> dict:
    """Run one check for real, return its result record with raw evidence."""
    name = chk.get("name") or chk["cmd"][:40]
    timeout = int(chk.get("timeout_s", DEFAULT_TIMEOUT))
    cwd = chk.get("cwd") or None
    expect_exit = int(chk.get("expect_exit", 0))
    # bash -o pipefail: without it, `pytest | tail` returns tail's exit code (0) and a failing
    # check scores as passing — the single most common false green in this kind of runner.
    wrapped = ["bash", "-o", "pipefail", "-c", chk["cmd"]]
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
    ap = argparse.ArgumentParser(add_help=True, description="Deterministic critic for the AI Test Loop")
    ap.add_argument("--config", required=True)
    ap.add_argument("--round", type=int)
    ap.add_argument("--json", action="store_true", help="also dump the full report as JSON to stdout")
    ap.add_argument("--quiet", action="store_true", help="only SCORE/GAPS/EVIDENCE lines")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show-state", action="store_true")
    args = ap.parse_args()

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

    print(f"SCORE={value}")
    print(f"THRESHOLD={threshold}")
    print(f"GATE={'passed' if gate else 'failed'} (round {round_no}/{cfg['max_rounds']})")
    print(f"GAPS={json.dumps(gaps, ensure_ascii=False)}")
    if soft:
        print(f"NON_BLOCKING_GAPS={json.dumps(soft, ensure_ascii=False)}")
    print(f"EVIDENCE={report_path}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
