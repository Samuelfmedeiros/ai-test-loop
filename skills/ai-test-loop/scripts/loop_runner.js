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

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const DEFAULT_TIMEOUT = 900;
const MAX_OUTPUT_CHARS = 8000;

const USAGE = `usage: ai-test-loop --repo <pasta|url>            monta o teste do projeto sozinho
       ai-test-loop --config <arquivo.json> [--round N] [--json] [--quiet]
       ai-test-loop --config <arquivo.json> --show-state
       ai-test-loop --config <arquivo.json> --dry-run`;

function die(msg, code = 2) {
  process.stderr.write(`[loop_runner] ${msg}\n`);
  process.exit(code);
}

function parseArgs(argv) {
  const out = {
    config: null,
    repo: null,
    round: null,
    json: false,
    quiet: false,
    dryRun: false,
    showState: false,
    approve: null,
    approveBy: null,
    migrateConfig: false,
    approveRunner: false,
    approveRunnerReason: null,
    help: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--config") {
      i += 1;
      if (i >= argv.length) die("--config needs a value\n" + USAGE);
      out.config = argv[i];
    } else if (a === "--repo") {
      i += 1;
      if (i >= argv.length) die("--repo needs a path or URL\n" + USAGE);
      out.repo = argv[i];
    } else if (a === "--round") {
      i += 1;
      const n = Number.parseInt(argv[i], 10);
      if (!Number.isFinite(n)) die("--round needs an integer\n" + USAGE);
      out.round = n;
    } else if (a === "--json") out.json = true;
    else if (a === "--quiet") out.quiet = true;
    else if (a === "--dry-run") out.dryRun = true;
    else if (a === "--show-state") out.showState = true;
    else if (a === "--approve") {
      i += 1;
      if (i >= argv.length) die("--approve needs an approval id\n" + USAGE);
      out.approve = argv[i];
    }
    else if (a === "--approve-by") {
      i += 1;
      if (i >= argv.length) die("--approve-by needs a value\n" + USAGE);
      out.approveBy = argv[i];
    } else if (a === "--migrate-config") out.migrateConfig = true;
    else if (a === "--approve-runner") out.approveRunner = true;
    else if (a === "--approve-runner-reason") {
      i += 1;
      if (i >= argv.length) die("--approve-runner-reason needs a value\n" + USAGE);
      out.approveRunnerReason = argv[i];
    }
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
  // Ambiguidade de gate e erro de config: `required: true` sem `severity` deixa
  // o check bloqueante ou so pontuavel a depender de quem le. Paridade com o .py.
  checks.forEach((chk) => {
    if (chk.required && !chk.severity) {
      die(
        `invalid config: check ${JSON.stringify(chk.name ?? String(chk.cmd).slice(0, 40))}: ` +
          'required=true sem severity — declare severity: "block" (bloqueia) ou "score" (so pontua)',
        1,
      );
    }
  });
  if (cfg.threshold === undefined) cfg.threshold = 85;
  if (cfg.max_rounds === undefined) cfg.max_rounds = 8;
  // 13/11/2026 — paridade com o runner Python: chave presente com `null` nao e
  // coberta por `=== undefined` e estourava em Number(null) => 0, aprovando
  // tudo (score >= 0). Normaliza explicitamente.
  if (cfg.threshold === null) cfg.threshold = 85;
  if (cfg.max_rounds === null) cfg.max_rounds = 8;
  // strict_tests liga a policia anti-cheat em TODOS os checks.
  if (cfg.strict_tests) {
    checks.forEach((chk) => {
      if (chk.allow_skipped === undefined || chk.allow_skipped === null) {
        chk.allow_skipped = false;
      }
    });
  }
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

function parseTestCounts(output) {
  const found = {};
  const bump = (k, n) => {
    found[k] = (found[k] || 0) + n;
  };
  const bumpRe = (re, mapKey) => {
    let m;
    while ((m = re.exec(output)) !== null) bump(mapKey ? mapKey(m[2]) : m[2], Number(m[1]));
  };
  bumpRe(/\b(\d+)\s+(passed|failed|skipped|xfailed|xpassed|errors?|error)\b/g, (k) =>
    k === "errors" || k === "error" ? "error" : k,
  );
  const testes = /Tests:\s*([^\n]+)/g;
  let tm;
  while ((tm = testes.exec(output)) !== null) {
    tm[1].split(",").forEach((part) => {
      const hit = /\s*(\d+)\s+(\w+)/.exec(part);
      if (hit) bump(hit[2], Number(hit[1]));
    });
  }
  const tap = /^#\s*(pass|fail|skip|todo|cancelled)\s+(\d+)\s*$/gm;
  let pm;
  while ((pm = tap.exec(output)) !== null) bump(pm[1], Number(pm[2]));
  if (Object.keys(found).length) return found;

  // Fallback: linha de progresso do pytest -q. Com warnings na sessao o pytest
  // suprime o sumario e imprime so `.s   [100%]`.
  const mapa = { ".": "passed", s: "skipped", x: "xfailed", X: "xpassed", F: "failed", E: "error" };
  output.split("\n").forEach((linha) => {
    const alvo = linha.trim();
    const m = /^([.sxXFE]+)\s*(\[\s*\d+%\]|$)/.exec(alvo);
    if (!m) return;
    for (const ch of m[1]) bump(mapa[ch], 1);
  });
  return found;
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

  // Anti-cheat (paridade com o .py): teste que nao roda nao e teste aprovado.
  const counts = {};
  const minPassed = chk.min_passed;
  const politica = chk.allow_skipped;
  if (minPassed !== undefined && minPassed !== null) {
    Object.assign(counts, parseTestCounts(combined));
    if (Object.keys(counts).length === 0) {
      reasons.push(
        `NOT VERIFIABLE: min_passed=${minPassed} exigido mas nenhum sumario de ` +
          "testes reconhecido na saida (pytest/jest/vitest/node:test)",
      );
    } else if ((counts.passed || 0) < Number(minPassed)) {
      reasons.push(`only ${counts.passed || 0} passing tests, min_passed=${minPassed}`);
    }
  }
  if (politica === false) {
    if (Object.keys(counts).length === 0) Object.assign(counts, parseTestCounts(combined));
    if (Object.keys(counts).length === 0) {
      reasons.push(
        "NOT VERIFIABLE: allow_skipped=false exige contagem de testes e nenhum " +
          "sumario nem linha de progresso foi reconhecida na saida",
      );
    } else {
      const pulados = ["skipped", "xfailed", "todo"]
        .filter((k) => counts[k])
        .map((k) => `${counts[k]} ${k}`);
      if (pulados.length) {
        reasons.push(
          `${pulados.join(", ")} — skip/xfail nao conta como aprovado (allow_skipped: false)`,
        );
      }
    }
  }

  return {
    name,
    cmd: chk.cmd,
    weight: Number(chk.weight),
    required: chk.required === undefined ? true : Boolean(chk.required),
    // severity: "block" falha DURA (exit 3), independente de peso/score.
    // Ausente = comportamento anterior (score puro) — compatibilidade total.
    severity: String(chk.severity === undefined ? "score" : chk.severity).trim().toLowerCase(),
    passed: reasons.length === 0,
    reasons,
    exit_code: rc,
    duration_s: duration,
    evidence: truncate(combined.trim()),
  };
}

// Schema da config — UNICA fonte de verdade do que entra no fingerprint.
// Paridade com TOP_LEVEL_KEYS/CHECK_KEYS do loop_runner.py. Lista literal
// atrasa o proximo adendo (medido 12/11/2026: strict_tests ficou de fora).
const TOP_LEVEL_KEYS = ["name", "threshold", "max_rounds", "strict_tests", "blocking_gaps", "version", "runner"];
const CHECK_KEYS = ["name", "cmd", "cwd", "junit_xml", "weight", "severity", "required", "expect_exit", "must_match", "fail_if_output_matches", "timeout_s", "min_passed", "allow_skipped", "erosion", "min_coverage"];
const FINGERPRINT_EXCLUDE = new Set(["name", "version", "runner", "comment", "notes"]);
const RUNNER_VERSION = "1.2.0";

function canonOf(obj, keys) {
  const out = {};
  for (const k of keys) {
    if (FINGERPRINT_EXCLUDE.has(k)) continue;
    if (!(k in obj)) continue;
    let v = obj[k];
    if (k === "strict_tests" || k === "allow_skipped") v = Boolean(v);
    out[k] = v;
  }
  return out;
}

function fingerprint(cfg) {
  const canon = canonOf(cfg, TOP_LEVEL_KEYS);
  canon.checks = (cfg.checks || []).map((c) => canonOf(c, CHECK_KEYS));
  return crypto.createHash("sha256").update(JSON.stringify(canon)).digest("hex");
}

function sha256File(p) {
  return crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
}

function runnerFingerprint() {
  const here = __dirname;
  const js = path.join(here, "loop_runner.js");
  const py = path.join(here, "loop_runner.py");
  return {
    version: RUNNER_VERSION,
    py_sha256: fs.existsSync(py) ? sha256File(py) : null,
    js_sha256: fs.existsSync(js) ? sha256File(js) : null,
  };
}

// ID de aprovacao DERIVADO do drift — o agente nao inventa o proprio bilhete.
function driftApprovalId(drift) {
  const canon = JSON.stringify({ e: drift.expected, a: drift.actual });
  return crypto.createHash("sha256").update(canon).digest("hex").slice(0, 8);
}

// Migracao de schema (1.2.0) — paridade com migrate_config() do .py.
// `required: true` sem `severity` virou erro de carga; quem atualiza por npx
// precisa de saida dentro do pacote, nao de uma mensagem mandando editar a mao.
function migrateConfig(cfg) {
  const mudancas = [];
  const checks = cfg.checks || [];
  checks.forEach((c, i) => {
    if (c.severity) return;
    if (c.required === true) {
      c.severity = "block";
      mudancas.push(`check '${c.name || c.cmd || "#" + i}': required=true -> severity="block"`);
    }
  });
  if (cfg.strict_tests === true) {
    checks.forEach((c, i) => {
      if (!("allow_skipped" in c)) {
        c.allow_skipped = false;
        mudancas.push(
          `check '${c.name || c.cmd || "#" + i}': allow_skipped ausente -> false ` +
            "(strict_tests: true ja implicava isso)",
        );
      }
    });
  }
  return { cfg, mudancas };
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
  if (!args.config && !args.repo) die("--repo <pasta|url> or --config <arquivo.json> is required\n" + USAGE);

  if (args.migrateConfig) {
    if (!args.config) die("--migrate-config precisa de --config <arquivo>");
    if (!fs.existsSync(args.config)) die(`config not found: ${args.config}`);
    let dados;
    try {
      dados = JSON.parse(fs.readFileSync(args.config, "utf8"));
    } catch (e) {
      die(`config is not valid JSON: ${e.message}`);
    }
    const { mudancas } = migrateConfig(dados);
    if (!mudancas.length) {
      process.stdout.write("[migrate] nada a migrar — config ja esta no schema 1.2\n");
      return 0;
    }
    for (const m of mudancas) process.stdout.write(`[migrate] ${m}\n`);
    if (args.dryRun) {
      process.stdout.write(
        `[migrate] DRY-RUN: ${mudancas.length} mudanca(s) NAO gravadas (${args.config})\n`,
      );
      return 0;
    }
    const backup = `${args.config}.bak-pre-migrate`;
    fs.copyFileSync(args.config, backup);
    const tmp = `${args.config}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(dados, null, 2) + "\n", "utf8");
    fs.renameSync(tmp, args.config);
    process.stdout.write(
      `[migrate] ${mudancas.length} mudanca(s) gravadas em ${args.config} ` +
        `(backup: ${backup.split("/").pop()})\n`,
    );
    return 0;
  }

  // Modo rápido: sem --config e com --repo, o teste se monta sozinho.
  let faltando = [];
  let achados = {};
  let repoRoot = null;
  if (args.repo && !args.config) {
    const probe = require("./repo_probe");
    repoRoot = probe.prepareRepo(args.repo);
    const [cfgAuto, falt, ach] = probe.buildConfig(repoRoot, args.repo);
    faltando = falt;
    achados = ach;
    if (!cfgAuto.checks.length) {
      process.stdout.write("Nenhum teste automático encontrado neste projeto.\n");
      for (const f of faltando) process.stdout.write(`  não deu para checar ${f.nome}: ${f.motivo}\n`);
      process.stdout.write("Rode com --config para apontar seus próprios comandos.\n");
      return 2;
    }
    const destino = path.join(probe.cacheDir(path.resolve(repoRoot)), "loop.json");
    fs.writeFileSync(destino, JSON.stringify(cfgAuto, null, 2));
    args.config = destino;
    // O modo rápido é uma foto do projeto AGORA, não uma campanha de rodadas:
    // reaproveitar o estado faria a segunda execução abortar com "max_rounds
    // exhausted" (medido 11/11/2026 no runner Python).
    for (const velho of [".loop-state.json", "loop-report-r1.json"]) {
      fs.rmSync(path.join(probe.cacheDir(path.resolve(repoRoot)), velho), { force: true });
    }
  }

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

  // Orcamento de tentativas impossivel de burlar: o round SEMPRE avanca.
  // Antes `roundNo = args.round` sem validar monotonicidade — o agente chamava
  // --round 1 dez vezes e o teto nunca disparava (paridade com o .py, 12/11/2026).
  const prevState = readJson(statePath);
  const prevCount = ((prevState && prevState.rounds) || []).length;
  let roundNo = args.round;
  let roundOverridden = false;
  if (roundNo === null || roundNo === undefined) {
    roundNo = prevCount + 1;
  } else {
    const pedido = Number(roundNo);
    roundNo = Math.max(prevCount + 1, pedido);
    roundOverridden = roundNo !== pedido;
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

  // Modo rápido: falta de dependência do projeto não é falha do projeto. Só neste
  // caminho — com --config o usuário declarou o que medir e a falha vale.
  let probe = null;
  if (args.repo) {
    probe = require("./repo_probe");
    const [mantidos, faltDep] = probe.reclassify(results);
    if (!mantidos.length) {
      process.stdout.write("Não deu para verificar nada: faltam dependências deste projeto.\n");
      for (const f of faltDep) process.stdout.write(`  - ${f.nome}: ${f.motivo}\n`);
      return 2;
    }
    results.length = 0;
    results.push(...mantidos);
    faltando = faltando.concat(faltDep);
  }

  const value = score(results);
  const threshold = Number(cfg.threshold);

  // Regua congelada (paridade com config_fingerprint do .py): hash DERIVADO do
  // schema, nao de lista literal. Medido 12/11/2026: lista literal deixava
  // strict_tests/min_passed/allow_skipped de fora e o agente desligava o
  // anti-cheat sem disparar drift.
  const fp = fingerprint(cfg);

  // Integridade do RUNNER: congelar a config sem congelar o codigo que a le
  // nao protege nada. Hash esperado vem do orquestrador (fora do repo) via env.
  const runnerFp = runnerFingerprint();
  const esperadoPy = process.env.ROGER_RUNNER_PY_SHA256 || null;
  let runnerDrift = Boolean(esperadoPy) && esperadoPy !== runnerFp.py_sha256;
  if (runnerDrift && args.approveRunner && args.approveRunnerReason) runnerDrift = false;

  const drift =
    prevState && prevState.config_sha256 && prevState.config_sha256 !== fp
      ? { expected: prevState.config_sha256, actual: fp }
      : null;

  // Bloqueio duro: check severity="block" que falhou impede entrega mesmo com
  // score acima do threshold (paridade com loop_runner.py, medido 12/11/2026).
  const blocking = results.filter((c) => !c.passed && c.severity === "block");
  const gaps = results
    .filter((c) => !c.passed && c.required)
    .map((c) => `${c.name}: ${c.reasons.join("; ")}`);
  const soft = results
    .filter((c) => !c.passed && !c.required)
    .map((c) => `${c.name}: ${c.reasons.join("; ")}`);

  // Aprovacao do drift: ID derivado do drift + --approve-by obrigatorio.
  // Medido 12/11/2026: `--approve id-inventado` re-baseava o config e liberava.
  const approveEsperado = drift ? driftApprovalId(drift) : null;
  const approveOk =
    Boolean(drift) && args.approve === approveEsperado && Boolean(args.approveBy);
  const driftInvalida = Boolean(drift) && !approveOk;
  let driftMotivo = null;
  if (drift && args.approve && !approveOk) {
    driftMotivo =
      args.approve !== approveEsperado
        ? `approval_id invalido: esperado '${approveEsperado}' (derivado do drift), recebido '${args.approve}'`
        : "--approve-by obrigatorio (aprovacao anonima nao audita)";
  }

  // `blocking_gaps`: gaps que INVALIDAM a entrega (nao so descontam peso).
  // Regex invalido bloqueia (fail-closed).
  const gapBlock = [];
  const padroes = cfg.blocking_gaps || [];
  if (padroes.length) {
    let rxs = [];
    try {
      rxs = padroes.map((p) => new RegExp(p, "i"));
    } catch (e) {
      gapBlock.push(`blocking_gaps invalido (${e.message}) — config recusada`);
    }
    for (const g of [...gaps, ...soft]) {
      if (rxs.some((r) => r.test(g))) gapBlock.push(g);
    }
  }

  const gate =
    value >= threshold && blocking.length === 0 && gapBlock.length === 0;

  const now = new Date();
  const ts = now.toISOString().replace(/\.\d{3}Z$/, "Z");
  const report = {
    project: cfg.name,
    round: roundNo,
    ts,
    threshold,
    blocking: blocking.map((c) => c.name),
    blocked: blocking.length > 0,
    score: value,
    gate: gate && !driftInvalida && !runnerDrift ? "passed" : "failed",
    checks: results,
    gaps,
    non_blocking_gaps: soft,
    blocking_gaps_hit: gapBlock,
    config_sha256: fp,
    runner: runnerFp,
    runner_drift: runnerDrift,
    drift_invalida_motivo: driftMotivo,
    config_drift: Boolean(drift),
    config_drift_detail: drift,
    drift_invalidates_gate: driftInvalida,
    round_overridden: roundOverridden,
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
    config_sha256: fp,
    config_drift: Boolean(drift),
    round_overridden: roundOverridden,
    drift_invalidates_gate: driftInvalida,
  });
  if (approveOk) {
    // Aprovar re-baseia o fingerprint; senao todo round seguinte continua
    // marcando drift da config antiga. Snapshot do drift aprovado vai junto:
    // sem ele auditar depois e impossivel (medido 12/11/2026).
    state.config_sha256 = fp;
    state.approval_id = args.approve;
    state.approval_by = args.approveBy;
    state.approval_ts = new Date().toISOString();
    state.approval_snapshot = {
      drift,
      approved_sha256: fp,
      previous_sha256: drift ? drift.expected : null,
      config_at_approval: {
        threshold: cfg.threshold,
        max_rounds: cfg.max_rounds,
        strict_tests: Boolean(cfg.strict_tests),
        blocking_gaps: cfg.blocking_gaps || null,
        checks: (cfg.checks || []).map((c) => ({
          name: c.name,
          weight: c.weight,
          severity: c.severity,
          allow_skipped: c.allow_skipped,
          min_passed: c.min_passed,
        })),
      },
    };
  } else if (!state.config_sha256) {
    state.config_sha256 = fp;
  }
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

  // Resumo em português de gente ANTES do bloco técnico: quem colou o projeto para
  // "ver se está bom" não é necessariamente programador. SCORE/GAPS continuam abaixo
  // porque CI e script de terceiro dependem deles.
  const inconclusivo = probe ? probe.inconclusive(results) : false;
  if (probe && !args.quiet) {
    process.stdout.write(probe.humanReport(cfg, faltando, achados, value, gate, results));
    process.stdout.write("\n");
  }

  process.stdout.write(`SCORE=${value}\n`);
  process.stdout.write(`THRESHOLD=${threshold}\n`);
  if (inconclusivo) {
    process.stdout.write("GATE=inconclusive (nada com peso cheio chegou a rodar — veja o que falta instalar)\n");
  } else {
    if (runnerDrift) {
      process.stdout.write(
        `GATE=blocked (round ${roundNo}/${cfg.max_rounds}) — motivo=runner_drift: o critic ` +
          `foi alterado no repo (py ${runnerFp.py_sha256.slice(0, 12)} != esperado ` +
          `${(esperadoPy || "").slice(0, 12)}); corrija o runner ou use --approve-runner ` +
          '--approve-runner-reason "<motivo>"\n',
      );
    } else if (driftInvalida) {
      process.stdout.write(
        `GATE=blocked (round ${roundNo}/${cfg.max_rounds}) — motivo=config_drift: regua ` +
          `alterada sem aprovacao (${drift.expected.slice(0, 12)} -> ${drift.actual.slice(0, 12)}); ` +
          `reexecute com --approve ${approveEsperado} --approve-by <nome>` +
          (driftMotivo ? ` [${driftMotivo}]` : "") + "\n",
      );
    } else if (gapBlock.length) {
      process.stdout.write(
        `GATE=blocked (round ${roundNo}/${cfg.max_rounds}) — motivo=blocking_gap: ` +
          `${gapBlock.length} gap(s) bloqueante(s): ${gapBlock.join("; ").slice(0, 300)}\n`,
      );
    } else if (blocking.length) {
      process.stdout.write(`GATE=blocked (round ${roundNo}/${cfg.max_rounds}) — check(s) severity=block falharam: ${blocking.map((c) => c.name).join(", ")}\n`);
    } else {
      process.stdout.write(`GATE=${gate ? "passed" : "failed"} (round ${roundNo}/${cfg.max_rounds})\n`);
    }
  }
  process.stdout.write(`GAPS=${JSON.stringify(gaps)}\n`);
  if (soft.length) process.stdout.write(`NON_BLOCKING_GAPS=${JSON.stringify(soft)}\n`);
  process.stdout.write(`EVIDENCE=${reportPath}\n`);
  if (args.json) process.stdout.write(JSON.stringify(report, null, 2) + "\n");
  if (inconclusivo) return 2;
  // Exit 3 = bloqueio duro (severity=block falhou). Distinto de 1 (nota baixa).
  if (blocking.length || driftInvalida || runnerDrift || gapBlock.length) return 3;
  return gate ? 0 : 1;
}

process.exit(main());
