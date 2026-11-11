#!/usr/bin/env node
"use strict";
/**
 * loop_runner — the deterministic critic of the AI Test Loop. Node port.
 *
 * Runs a list of weighed checks for real, captures their raw output as evidence, and prints:
 *
 *     SCORE=<0-100>
 *     GAPS=["<check>: <why it failed>", ...]
 *     EVIDENCE=<path to the JSON report>
 *
 * Why this file exists: `npx ai-test-loop` was a wrapper around a Python script, so the
 * package's best call to action died with "Python 3 not found" on a clean machine (no venv,
 * no python on PATH). Measured 11/11/2026. The runner is now Node, so `npx` needs nothing
 * but the node that npx already requires. The Python runner stays in scripts/ for pip users.
 *
 * Deliberately dumb: exit codes + optional output patterns decide pass/fail. No model calls,
 * no network, no telemetry.
 *
 * Usage:
 *     ai-test-loop --config loop.json [--round 2] [--json] [--quiet]
 *     ai-test-loop --config loop.json --show-state
 *     ai-test-loop --config loop.json --dry-run
 *
 * Exit code: 0 when SCORE >= threshold (gate passed), 1 when below, 2 on usage/config error.
 */

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const DEFAULT_TIMEOUT = 900;
const MAX_OUTPUT_CHARS = 8000;

const USAGE = `usage: ai-test-loop --config <arquivo.json> [--round N] [--json] [--quiet]
       ai-test-loop --config <arquivo.json> --show-state
       ai-test-loop --config <arquivo.json> --dry-run`;

function die(msg, code = 2) {
  process.stderr.write(`[loop_runner] ${msg}\n`);
  process.exit(code);
}

function parseArgs(argv) {
  const out = {
    config: null,
    round: null,
    json: false,
    quiet: false,
    dryRun: false,
    showState: false,
    help: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--config") {
      i += 1;
      if (i >= argv.length) die("--config needs a value\n" + USAGE);
      out.config = argv[i];
    } else if (a === "--round") {
      i += 1;
      const n = Number.parseInt(argv[i], 10);
      if (!Number.isFinite(n)) die("--round needs an integer\n" + USAGE);
      out.round = n;
    } else if (a === "--json") out.json = true;
    else if (a === "--quiet") out.quiet = true;
    else if (a === "--dry-run") out.dryRun = true;
    else if (a === "--show-state") out.showState = true;
    else if (a === "--help" || a === "-h") out.help = true;
    else die(`unknown argument ${JSON.stringify(a)}\n${USAGE}`);
  }
  return out;
}

function loadConfig(cfgPath) {
  let raw;
  try {
    raw = fs.readFileSync(cfgPath, "utf8");
  } catch (_) {
    die(`config not found: ${cfgPath}`, 2);
  }
  let cfg;
  try {
    cfg = JSON.parse(raw);
  } catch (exc) {
    die(`config is not valid JSON: ${exc.message}`, 2);
  }
  const checks = cfg.checks;
  if (!Array.isArray(checks) || checks.length === 0) {
    die("config needs a non-empty 'checks' list", 2);
  }
  checks.forEach((chk, i) => {
    if (!chk || !chk.cmd) die(`check #${i} has no 'cmd'`, 2);
    const w = chk.weight;
    if (typeof w !== "number" || !(w > 0)) {
      die(`check ${JSON.stringify(chk.name ?? i)} needs a positive 'weight'`, 2);
    }
  });
  if (cfg.name === undefined || cfg.name === null) {
    cfg.name = path.basename(path.dirname(cfgPath)) || "loop";
  }
  if (cfg.threshold === undefined) cfg.threshold = 85;
  if (cfg.max_rounds === undefined) cfg.max_rounds = 8;
  return cfg;
}

// bash -o pipefail: without it, `pytest | tail` returns tail's exit code (0) and a failing
// check scores as passing — the single most common false green in this kind of runner.
// Windows has no bash by default: fall back to cmd.exe and say so, instead of silently
// dropping pipefail semantics.
let _shell = null;
function shellFor() {
  if (_shell) return _shell;
  const probe = spawnSync("bash", ["-c", "exit 0"], { stdio: "ignore" });

  if (process.platform === "win32") {
    // Windows has no bash by default: use it when present (Git Bash / WSL), otherwise
    // cmd.exe and say so, instead of silently dropping pipefail semantics.
    _shell = probe.status === 0
      ? { cmd: "bash", prefix: ["-o", "pipefail", "-c"], pipefail: true }
      : { cmd: (process.env.ComSpec || "cmd.exe"), prefix: ["/d", "/s", "/c"], pipefail: false };
    return _shell;
  }

  // POSIX: never fall back to cmd.exe. Without bash (minimal containers, stripped PATH)
  // use /bin/sh, which is guaranteed by POSIX — it just cannot honour pipefail.
  _shell = probe.status === 0
    ? { cmd: "bash", prefix: ["-o", "pipefail", "-c"], pipefail: true }
    : { cmd: "/bin/sh", prefix: ["-c"], pipefail: false };
  return _shell;
}

function truncate(text) {
  if (text.length <= MAX_OUTPUT_CHARS) return text;
  const half = Math.floor(MAX_OUTPUT_CHARS / 2);
  return `${text.slice(0, half)}\n...[truncated]...\n${text.slice(-half)}`;
}

function runCheck(chk) {
  const name = chk.name || String(chk.cmd).slice(0, 40);
  const timeout = Number.parseInt(chk.timeout_s ?? DEFAULT_TIMEOUT, 10);
  const timeoutMs = (Number.isFinite(timeout) ? timeout : DEFAULT_TIMEOUT) * 1000;
  const expectExit = Number.parseInt(chk.expect_exit ?? 0, 10);
  const shell = shellFor();

  const started = Date.now();
  const proc = spawnSync(shell.cmd, [...shell.prefix, chk.cmd], {
    cwd: chk.cwd || undefined,
    encoding: "utf8",
    timeout: timeoutMs,
    killSignal: "SIGKILL",
    maxBuffer: 64 * 1024 * 1024,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });

  const duration = Math.round((Date.now() - started) / 10) / 100;
  const timedOut = proc.error && (proc.error.code === "ETIMEDOUT" || proc.signal === "SIGKILL");
  const rc = timedOut ? null : proc.status;
  const out = proc.stdout || "";
  const err = proc.stderr || "";
  // spawn failure (ENOENT etc.) is a check failure, never a skip
  const spawnError = proc.error && !timedOut ? String(proc.error.message || proc.error.code) : "";

  const combined = out + (err ? `\n${err}` : "");

  const reasons = [];
  if (timedOut) reasons.push(`timed out after ${timeout}s`);
  else if (spawnError) reasons.push(`could not run: ${spawnError}`);
  else if (rc !== expectExit) reasons.push(`exit code ${rc}, expected ${expectExit}`);

  if (chk.must_match) {
    let re;
    try {
      re = new RegExp(chk.must_match, "m");
    } catch (exc) {
      reasons.push(`invalid must_match regex /${chk.must_match}/: ${exc.message}`);
      re = null;
    }
    if (re && !re.test(combined)) {
      reasons.push(`output does not match required pattern /${chk.must_match}/`);
    }
  }

  if (chk.fail_if_output_matches) {
    let re;
    try {
      re = new RegExp(chk.fail_if_output_matches, "m");
    } catch (exc) {
      reasons.push(
        `invalid fail_if_output_matches regex /${chk.fail_if_output_matches}/: ${exc.message}`,
      );
      re = null;
    }
    if (re) {
      const hit = re.exec(combined);
      if (hit) {
        reasons.push(
          `output matched failure pattern /${chk.fail_if_output_matches}/: ${JSON.stringify(hit[0])}`,
        );
      }
    }
  }

  return {
    name,
    cmd: chk.cmd,
    weight: Number(chk.weight),
    required: chk.required === undefined ? true : Boolean(chk.required),
    passed: reasons.length === 0,
    reasons,
    exit_code: rc,
    duration_s: duration,
    evidence: truncate(combined.trim()),
  };
}

function score(checks) {
  const total = checks.reduce((acc, c) => acc + c.weight, 0) || 1.0;
  const earned = checks.reduce((acc, c) => acc + (c.passed ? c.weight : 0), 0);
  // Uma casa decimal, sempre — o runner Python imprime "SCORE=50.0" e o Node precisa dizer
  // a mesma coisa. `50` e `50.0` no mesmo campo obrigam quem faz parse a tratar dois tipos.
  return (Math.round((earned / total) * 1000) / 10).toFixed(1);
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (_) {
    return null;
  }
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(USAGE + "\n");
    return 0;
  }
  if (!args.config) die("--config is required\n" + USAGE);

  const cfgPath = path.resolve(args.config);
  const cfg = loadConfig(cfgPath);
  const dir = path.dirname(cfgPath);
  // Per-config filenames: two configs in the same folder must not share one state file,
  // otherwise rounds from project A pollute project B's history and round numbers.
  const stem = path.basename(cfgPath, path.extname(cfgPath));
  const statePath = path.join(dir, `.${stem}-state.json`);

  if (args.showState) {
    if (!fs.existsSync(statePath)) {
      process.stdout.write("[loop_runner] no state file yet\n");
      return 0;
    }
    const st = readJson(statePath) || {};
    for (const r of st.rounds || []) {
      const failed = (r.checks || []).filter((c) => !c.passed).map((c) => c.name);
      process.stdout.write(
        `round ${r.round}: SCORE=${r.score} threshold=${r.threshold} failed=${JSON.stringify(failed)}\n`,
      );
    }
    return 0;
  }

  if (args.dryRun) {
    for (const c of cfg.checks) {
      const nm = c.name || String(c.cmd).slice(0, 40);
      process.stdout.write(`[dry-run] ${nm}  weight=${c.weight}  cmd=${c.cmd}\n`);
    }
    process.stdout.write(`[dry-run] threshold=${cfg.threshold} max_rounds=${cfg.max_rounds}\n`);
    return 0;
  }

  let roundNo = args.round;
  if (roundNo === null || roundNo === undefined) {
    const prev = readJson(statePath);
    roundNo = ((prev && prev.rounds) || []).length + 1;
  }

  if (roundNo > Number(cfg.max_rounds)) {
    process.stdout.write(`ROUND: ${roundNo}  ABORT: exceeded max_rounds=${cfg.max_rounds}\n`);
    process.stdout.write(
      'GAPS=["round budget exhausted — the check set or the threshold needs revisiting"]\n',
    );
    return 1;
  }

  const results = [];
  for (const chk of cfg.checks) {
    if (!args.quiet) {
      const nm = chk.name || String(chk.cmd).slice(0, 40);
      process.stderr.write(`[loop_runner] round ${roundNo}: running ${JSON.stringify(nm)} ...\n`);
    }
    results.push(runCheck(chk));
  }

  const value = score(results);
  const threshold = Number(cfg.threshold);
  const gate = value >= threshold;
  const gaps = results
    .filter((c) => !c.passed && c.required)
    .map((c) => `${c.name}: ${c.reasons.join("; ")}`);
  const soft = results
    .filter((c) => !c.passed && !c.required)
    .map((c) => `${c.name}: ${c.reasons.join("; ")}`);

  const now = new Date();
  const ts = now.toISOString().replace(/\.\d{3}Z$/, "Z");
  const report = {
    project: cfg.name,
    round: roundNo,
    ts,
    threshold,
    score: value,
    gate: gate ? "passed" : "failed",
    checks: results,
    gaps,
    non_blocking_gaps: soft,
  };
  const reportPath = path.join(dir, `${stem}-report-r${roundNo}.json`);
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), "utf8");

  const state = readJson(statePath) || { project: cfg.name, rounds: [] };
  if (!Array.isArray(state.rounds)) state.rounds = [];
  state.project = state.project || cfg.name;
  state.rounds.push({
    round: roundNo,
    score: value,
    threshold,
    gate: report.gate,
    ts,
    report: reportPath,
    checks: results,
  });
  fs.writeFileSync(statePath, JSON.stringify(state, null, 2), "utf8");

  if (!args.quiet) {
    for (const c of results) {
      const status = c.passed ? "PASS" : "FAIL";
      const lines = c.evidence.trim().split("\n");
      const detail = (lines[lines.length - 1] || "(no output)").slice(0, 160);
      process.stdout.write(
        `  ${status}  ${c.name.padEnd(28)} w=${String(c.weight).padEnd(5)} ${String(c.duration_s).padStart(7)}s  ${detail}\n`,
      );
    }
  }

  process.stdout.write(`SCORE=${value}\n`);
  process.stdout.write(`THRESHOLD=${threshold}\n`);
  process.stdout.write(`GATE=${gate ? "passed" : "failed"} (round ${roundNo}/${cfg.max_rounds})\n`);
  process.stdout.write(`GAPS=${JSON.stringify(gaps)}\n`);
  if (soft.length) process.stdout.write(`NON_BLOCKING_GAPS=${JSON.stringify(soft)}\n`);
  process.stdout.write(`EVIDENCE=${reportPath}\n`);
  if (args.json) process.stdout.write(JSON.stringify(report, null, 2) + "\n");
  return gate ? 0 : 1;
}

process.exit(main());
