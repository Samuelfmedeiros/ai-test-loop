#!/usr/bin/env node
"use strict";
// selftest — prova que o runner funciona, sem depender de Python.
// Roda casos que cobrem os quatro jeitos de um check falhar (exit, must_match,
// fail_if_output_matches, timeout) mais o caminho feliz, e compara com o esperado.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const runner = path.join(__dirname, "loop_runner.js");
const cli = path.join(__dirname, "..", "bin", "cli.js");
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "atl-selftest-"));

let falhas = 0;
function check(nome, cond, detalhe) {
  if (cond) {
    console.log(`  ok    ${nome}`);
  } else {
    falhas += 1;
    console.log(`  FALHA ${nome}${detalhe ? " — " + detalhe : ""}`);
  }
}

function roda(alvo, args, cfg) {
  const cfgPath = path.join(dir, `c${Math.random().toString(36).slice(2)}.json`);
  fs.writeFileSync(cfgPath, JSON.stringify(cfg), "utf8");
  // Gate failed é exit 1 DE PROPÓSITO: o runner sinaliza reprovação no exit code para CI.
  // Sem capturar isso, o selftest morre justamente nos casos que ele quer testar.
  try {
    return execFileSync(process.execPath, [alvo, "--config", cfgPath, ...args], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (e) {
    if (e.stdout !== undefined && e.stdout !== null) return String(e.stdout);
    throw e;
  }
}

function campo(out, nome) {
  const m = out.match(new RegExp(`^${nome}=(.*)$`, "m"));
  return m ? m[1] : null;
}

// ---------------------------------------------------------------- runner
console.log("runner — caminho feliz");
{
  const cfg = { name: "t", threshold: 85, checks: [{ name: "ok", cmd: "echo hi", weight: 100 }] };
  const out = roda(runner, ["--quiet"], cfg);
  check("SCORE=100.0", campo(out, "SCORE") === "100.0", campo(out, "SCORE"));
  check("GATE=passed", (campo(out, "GATE") || "").startsWith("passed"));
  check("GAPS vazio", campo(out, "GAPS") === "[]");
  check("EVIDENCE aponta relatório", /report-r1\.json$/.test(campo(out, "EVIDENCE") || ""));
}

console.log("runner — exit code errado zera o score");
{
  const cfg = { name: "t", threshold: 85, checks: [{ name: "rc", cmd: "exit 4", weight: 100 }] };
  const out = roda(runner, ["--quiet"], cfg);
  check("SCORE=0.0", campo(out, "SCORE") === "0.0", campo(out, "SCORE"));
  check("GATE=failed", (campo(out, "GATE") || "").startsWith("failed"));
  check("gap cita exit code 4", (campo(out, "GAPS") || "").includes("exit code 4"));
}

console.log("runner — must_match");
{
  const cfg = {
    name: "t",
    threshold: 50,
    checks: [{ name: "m", cmd: "echo nada", must_match: "esperado", weight: 100 }],
  };
  const out = roda(runner, ["--quiet"], cfg);
  check("SCORE=0.0", campo(out, "SCORE") === "0.0", campo(out, "SCORE"));
  check("gap cita o padrão", (campo(out, "GAPS") || "").includes("does not match required pattern"));
}

console.log("runner — fail_if_output_matches");
{
  const cfg = {
    name: "t",
    threshold: 50,
    checks: [{
      name: "f",
      cmd: "echo 'leaks found: 3'",
      fail_if_output_matches: "leaks found: [1-9]",
      weight: 100,
    }],
  };
  const out = roda(runner, ["--quiet"], cfg);
  check("SCORE=0.0", campo(out, "SCORE") === "0.0", campo(out, "SCORE"));
  check("gap cita failure pattern", (campo(out, "GAPS") || "").includes("failure pattern"));
}

console.log("runner — timeout é FAIL, nunca skip");
{
  const cfg = {
    name: "t",
    threshold: 50,
    checks: [{ name: "lento", cmd: "sleep 5", timeout_s: 1, weight: 100 }],
  };
  const out = roda(runner, ["--quiet"], cfg);
  check("SCORE=0.0", campo(out, "SCORE") === "0.0", campo(out, "SCORE"));
  check("gap cita timed out", (campo(out, "GAPS") || "").includes("timed out after 1s"));
}

console.log("runner — required:false não bloqueia");
{
  const cfg = {
    name: "t",
    threshold: 70,
    checks: [
      { name: "duro", cmd: "echo ok", weight: 70 },
      { name: "mole", cmd: "exit 1", weight: 30, required: false },
    ],
  };
  const out = roda(runner, [], cfg);
  check("SCORE=70.0", campo(out, "SCORE") === "70.0", campo(out, "SCORE"));
  check("GATE=passed", (campo(out, "GATE") || "").startsWith("passed"));
  check("gap vai para NON_BLOCKING", (campo(out, "NON_BLOCKING_GAPS") || "").includes("mole"));
}

console.log("runner — pipefail: `falha | tail` não vira verde");
{
  const cfg = {
    name: "t",
    threshold: 50,
    checks: [{ name: "pipe", cmd: "exit 1 | tail -1", weight: 100 }],
  };
  const out = roda(runner, ["--quiet"], cfg);
  check("SCORE=0.0 (pipefail ativo)", campo(out, "SCORE") === "0.0", campo(out, "SCORE"));
}

console.log("runner — config inválida sai com 2");
{
  let code = 0;
  const cfgPath = path.join(dir, "ruim.json");
  fs.writeFileSync(cfgPath, JSON.stringify({ checks: [] }), "utf8");
  try {
    execFileSync(process.execPath, [runner, "--config", cfgPath], { stdio: "ignore" });
  } catch (e) {
    code = e.status;
  }
  check("exit 2", code === 2, String(code));
}

// ---------------------------------------------------------------- cli
console.log("cli — --version e --help saem sem Python");
{
  const v = execFileSync(process.execPath, [cli, "--version"], {
    encoding: "utf8",
    env: { ...process.env, AI_TEST_LOOP_NO_TELEMETRY: "1" },
  });
  check("--version imprime semver", /^\d+\.\d+\.\d+$/m.test(v.trim()), v.trim());
  // Regressão medida 11/11/2026: o wrapper imprimia a versão e caía no runner, que respondia
  // "unknown argument --version". A saída tem que ser SÓ a versão.
  check("--version não vaza erro do runner", !v.includes("unknown argument"), JSON.stringify(v.slice(0, 120)));

  const h = execFileSync(process.execPath, [cli, "--help"], {
    encoding: "utf8",
    env: { ...process.env, AI_TEST_LOOP_NO_TELEMETRY: "1" },
  });
  check("--help fala do produto", h.includes("nota 0-100"));
  check("--help aponta para a loja", h.includes("skills.seu.pet"));
}

console.log("cli — run real ponta a ponta");
{
  const cfgPath = path.join(dir, "e2e.json");
  fs.writeFileSync(cfgPath, JSON.stringify({
    name: "e2e", threshold: 85, checks: [{ name: "ok", cmd: "echo done", weight: 100 }],
  }), "utf8");
  const out = execFileSync(process.execPath, [cli, "--config", cfgPath, "--quiet"], {
    encoding: "utf8",
    env: { ...process.env, AI_TEST_LOOP_NO_TELEMETRY: "1" },
  });
  check("SCORE=100.0 pelo cli", campo(out, "SCORE") === "100.0", campo(out, "SCORE"));
}

console.log("shell — sem bash no PATH nao cai para cmd.exe no POSIX");
{
  // Reproduz o ambiente magro que quebrou o 1.1.0: PATH sem bash e sem python.
  // No Linux/macOS o runner tem de usar /bin/sh; cair em cmd.exe faz TODO check
  // falhar com "spawnSync cmd.exe ENOENT" e o gate reprova o usuario inocente.
  const cfgPath = path.join(dir, "noshell.json");
  fs.writeFileSync(cfgPath, JSON.stringify({
    name: "noshell", threshold: 85,
    checks: [{ name: "ok", cmd: "echo sem-bash", weight: 100 }],
  }), "utf8");
  const magro = path.join(dir, "magro");
  fs.mkdirSync(magro, { recursive: true });
  fs.symlinkSync(process.execPath, path.join(magro, "node"));
  if (process.platform !== "win32") {
    const out = execFileSync(process.execPath, [cli, "--config", cfgPath], {
      encoding: "utf8",
      env: { PATH: magro, HOME: dir, AI_TEST_LOOP_NO_TELEMETRY: "1", TERM: "dumb" },
    });
    check("POSIX sem bash: check roda", campo(out, "SCORE") === "100.0", campo(out, "SCORE"));
    check("POSIX sem bash: gate passa", campo(out, "GATE").startsWith("passed"), campo(out, "GATE"));
  } else {
    check("POSIX sem bash: n/a no Windows", true, "skip");
  }
}

fs.rmSync(dir, { recursive: true, force: true });
console.log(falhas === 0 ? "\nTUDO OK" : `\n${falhas} FALHA(S)`);
process.exit(falhas === 0 ? 0 : 1);
