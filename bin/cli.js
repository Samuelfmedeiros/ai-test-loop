#!/usr/bin/env node
// Wrapper do critic determinístico (Python) para uso via `npx ai-test-loop`.
//
// Por que existe: o runner é Python puro (stdlib) e a porta de entrada que dev
// usa é `npx`. O wrapper acha o interpretador, repassa os argumentos e — desde
// 1.0.2 — registra UM evento anônimo por execução, para o pacote deixar de ser
// cego (555 downloads no mês e nenhum sinal de uso real).
//
// Privacidade: nada de IP gravado, nada de e-mail, nada de caminho de projeto,
// NUNCA o conteúdo do comando ou do código do usuário. Só: evento
// (run/help/erro/version), versão, versão de node/python, SO, duração e — quando
// é um run de verdade — score e número de gaps.
//
// Opt-out, nesta ordem:
//   --no-telemetry                 naquele run
//   AI_TEST_LOOP_NO_TELEMETRY=1    permanente
//   DO_NOT_TRACK=1                 padrão do ecossistema
//   CI=1                           integração contínua não é pessoa: não conta
// Se a rede falhar ou o servidor estiver fora, o CLI segue normal: telemetria
// nunca bloqueia, nunca espera e nunca aparece na saída.
const { spawn, execSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const https = require("node:https");
const crypto = require("node:crypto");

const VERSAO = require(path.join(__dirname, "..", "package.json")).version;
const URL_EVENTO = process.env.AI_TEST_LOOP_TELEMETRY_URL
  || "https://skills.seu.pet/api/cli";
const TIMEOUT_MS = 2500;

const script = path.join(__dirname, "..", "scripts", "loop_runner.py");
if (!fs.existsSync(script)) {
  console.error("ai-test-loop: runner não encontrado em " + script);
  process.exit(2);
}

const argv = process.argv.slice(2);

// `--no-telemetry` e flag do WRAPPER, nao do runner Python: repassar faz o
// argparse morrer com "unrecognized arguments" (achado no smoke test, 08/11).
// Por isso ele sai da lista antes de qualquer spawn.
const argvRunner = argv.filter((a) => a !== "--no-telemetry");

// ---------------------------------------------------------------- telemetria
function desligada() {
  if (argv.includes("--no-telemetry")) return true;
  const v = (k) => String(process.env[k] || "").toLowerCase();
  if (["1", "true", "yes"].includes(v("AI_TEST_LOOP_NO_TELEMETRY"))) return true;
  if (["1", "true", "yes"].includes(v("DO_NOT_TRACK"))) return true;
  if (process.env.CI) return true;
  return false;
}

// Id aleatório local: conta MÁQUINAS, não pessoas, e não liga a nada do usuário.
// Fica em ~/.ai-test-loop/telemetry-id. Apagar o arquivo = id novo.
function idInstalacao() {
  try {
    const dir = path.join(os.homedir(), ".ai-test-loop");
    const arq = path.join(dir, "telemetry-id");
    if (fs.existsSync(arq)) {
      const lido = fs.readFileSync(arq, "utf8").trim();
      if (/^[0-9a-f-]{36}$/.test(lido)) return lido;
    }
    const novo = crypto.randomUUID();
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(arq, novo + "\n", { mode: 0o600 });
    return novo;
  } catch (_) {
    return ""; // sem id, o servidor grava o evento como anônimo
  }
}

function modoDe(args) {
  if (args.includes("--help") || args.includes("-h")) return "help";
  if (args.includes("--version") || args.includes("-v")) return "version";
  if (args.includes("--dry-run")) return "dry-run";
  if (args.includes("--show-state")) return "show-state";
  return "run";
}

function enviar(payload) {
  // Devolve Promise: `process.exit()` mata o processo antes do POST terminar, e
  // o evento se perdia (medido 08/11: dry-run nao gravava nada). Quem chama
  // espera com `.finally` antes de sair.
  if (desligada()) return Promise.resolve();
  return new Promise((resolve) => {
  try {
    const corpo = Buffer.from(JSON.stringify(payload));
    const u = new URL(URL_EVENTO);
    const lib = u.protocol === "http:" ? require("node:http") : https;
    const req = lib.request({
      hostname: u.hostname,
      port: u.port || (u.protocol === "http:" ? 80 : 443),
      path: u.pathname + u.search,
      method: "POST",
      headers: {
        "content-type": "application/json",
        "content-length": corpo.length,
        "user-agent": `ai-test-loop/${VERSAO}`,
      },
      timeout: TIMEOUT_MS,
    }, (res) => { res.resume(); res.on("end", resolve); });
    req.on("error", () => resolve());   // rede ruim nunca afeta o usuário
    req.on("timeout", () => { req.destroy(); resolve(); });
    req.end(corpo);
  } catch (_) { resolve(); }  // telemetria é acessório: falha em silêncio
  });
}

function base(evento, modo) {
  return {
    instalacao: idInstalacao(),
    evento,
    versao: VERSAO,
    node: process.versions.node.split(".")[0],
    so: process.platform,
    modo,
  };
}

// ---------------------------------------------------------------- execução
const modo = modoDe(argv);

// --help e --version saem antes de qualquer run: o evento mostra que a pessoa
// chegou a perguntar como usar, que é o degrau mais barato do funil.
const envioInicial = (modo === "help" || modo === "version")
  ? enviar(base(modo, modo))
  : Promise.resolve();

const candidatos = process.env.AI_TEST_LOOP_PYTHON
  ? [process.env.AI_TEST_LOOP_PYTHON]
  : ["python3", "python"];

const inicio = Date.now();
let saidaScore = "";   // o runner imprime SCORE=<n> na saída
let saidaGaps = "";

function tentar(i) {
  if (i >= candidatos.length) {
    console.error("ai-test-loop: Python 3 não encontrado (tente AI_TEST_LOOP_PYTHON=/caminho/python3)");
    enviar(Object.assign(base("erro", modo), { python: "ausente" }))
      .finally(() => process.exit(2));
    return;
  }
  const p = spawn(candidatos[i], [script, ...argvRunner], { stdio: ["inherit", "pipe", "pipe"] });

  // Repassa a saída ao usuário E lê SCORE/GAPS para o evento. Precisa ser pipe:
  // com stdio herdado não daria para ler, e o evento ficaria sem o resultado.
  p.stdout.on("data", (b) => {
    process.stdout.write(b);
    const t = b.toString();
    if (!saidaScore) { const m = t.match(/SCORE=(\d+)/); if (m) saidaScore = m[1]; }
    if (!saidaGaps) { const m = t.match(/GAPS=(\d+)/); if (m) saidaGaps = m[1]; }
  });
  p.stderr.on("data", (b) => process.stderr.write(b));

  p.on("error", () => tentar(i + 1));
  p.on("exit", (code, signal) => {
    if (modo !== "help" && modo !== "version") {
      let pyMajor = "";
      try { pyMajor = execSync(`${candidatos[i]} -V`, { encoding: "utf8" }).trim().split(" ")[1].split(".")[0]; }
      catch (_) { /* sem python legível: campo fica vazio */ }
      enviar(Object.assign(base(code === 0 ? "run" : "erro", modo), {
        duracao_ms: Date.now() - inicio,
        score: saidaScore || undefined,
        gaps: saidaGaps || undefined,
        python: pyMajor || undefined,
      })).finally(() => process.exit(signal ? 1 : (code ?? 0)));
      return;
    }
    process.exit(signal ? 1 : (code ?? 0));
  });
}

tentar(0);
