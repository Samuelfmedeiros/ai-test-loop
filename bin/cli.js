#!/usr/bin/env node
// Entry point do critic determinístico para uso via `npx ai-test-loop`.
//
// Por que existe: o runner é um script que roda de verdade checagens pesadas (exit code +
// padrões de saída). O npx é a porta de entrada mais barata que temos, então ele precisa
// funcionar numa máquina limpa.
//
// MEDIDO 11/11/2026: a versão anterior era um wrapper que chamava `loop_runner.py` com
// python3/python. Numa máquina sem Python no PATH o comando morria com
// "ai-test-loop: Python 3 não encontrado" — o melhor CTA do produto virava a pior primeira
// impressão. Agora o runner tem porte em Node (scripts/loop_runner.js) e o npx não depende
// de mais nada além do próprio node. O runner Python continua no pacote para quem instala
// via pip; os dois imprimem o mesmo SCORE/GAPS/EVIDENCE.
//
// Telemetria: UM evento anônimo por execução, para o pacote deixar de ser cego.
// Privacidade: nada de IP, nada de e-mail, nada de caminho de projeto, NUNCA o conteúdo do
// comando ou do código do usuário. Só: evento (run/help/erro/version), versão, versão de
// node, SO, duração e — em run de verdade — score e número de gaps.
//
// Opt-out, nesta ordem:
//   --no-telemetry                 naquele run
//   AI_TEST_LOOP_NO_TELEMETRY=1    permanente
//   DO_NOT_TRACK=1                 padrão do ecossistema
//   CI=1                           integração contínua não é pessoa: não conta
// Rede falhou ou servidor fora: o CLI segue normal — telemetria nunca bloqueia, nunca
// espera e nunca aparece na saída.
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const https = require("node:https");
const crypto = require("node:crypto");

const VERSAO = require(path.join(__dirname, "..", "package.json")).version;
const URL_EVENTO = process.env.AI_TEST_LOOP_TELEMETRY_URL
  || "https://skills.seu.pet/api/cli";
const TIMEOUT_MS = 2500;

const runnerJs = path.join(__dirname, "..", "scripts", "loop_runner.js");
const runnerPy = path.join(__dirname, "..", "scripts", "loop_runner.py");

const argv = process.argv.slice(2);

// `--no-telemetry` é flag do WRAPPER, não do runner: repassar faz o argparse do Python
// morrer com "unrecognized arguments" (achado no smoke test, 08/11). Sai da lista antes
// de qualquer spawn.
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
  // Devolve Promise: `process.exit()` mata o processo antes do POST terminar, e o evento se
  // perdia (medido 08/11: dry-run não gravava nada). Quem chama espera com `.finally`.
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

// ---------------------------------------------------------------- ajuda
// Quem roda `npx ai-test-loop@latest` sem argumento precisa descobrir o que a ferramenta
// faz e o caminho mais curto para ver a nota — não o usage cru do runner.
const AJUDA = `
ai-test-loop — nota 0-100 no seu agente antes de entregar.

USO
  ai-test-loop --config <arquivo.json>       roda as checagens e imprime a nota
  ai-test-loop --config <arq> --dry-run      mostra o que rodaria, sem executar
  ai-test-loop --config <arq> --quiet        só SCORE/GAPS/EVIDENCE
  ai-test-loop --config <arq> --json         relatório completo em JSON
  ai-test-loop --show-state                  última rodada e placar guardados
  ai-test-loop --version                     versão do pacote
  ai-test-loop --help                        esta ajuda

PRIMEIRA VEZ
  1. copie a config de exemplo:   npx ai-test-loop@latest --config loop.example.json --dry-run
  2. declare suas checagens (nome, comando, peso, o que significa passar)
  3. rode sem --dry-run e receba a nota, com os gaps que faltam

OPÇÕES DO WRAPPER
  --no-telemetry                 não envia evento anônimo nesta execução
  AI_TEST_LOOP_NO_TELEMETRY=1    desliga permanentemente

Não precisa de Python: o runner é Node puro. Não faz deploy, não faz merge e não abre rede.

Pacote completo, packs e suporte: https://skills.seu.pet/p/ai-test-loop?utm_source=cli&utm_medium=help&utm_campaign=ai-test-loop
`;

// ---------------------------------------------------------------- execução
const modo = modoDe(argv);
const semArgumentos = argvRunner.length === 0;

// Saídas que NÃO chegam no runner: --help, --version e o "sem argumento" (que mostra a
// ajuda). Sem esta guarda o fluxo continua depois do `console.log` e o runner recebe
// `--version`, imprimindo "unknown argument" logo abaixo da versão (medido 11/11/2026 no
// tarball 1.1.0 instalado numa máquina limpa).
const saiAntesDoRunner = modo === "help" || modo === "version" || semArgumentos;

const envioInicial = (modo === "help" || modo === "version" || semArgumentos)
  ? enviar(base(semArgumentos ? "help" : modo, modo))
  : Promise.resolve();

if (modo === "help" || semArgumentos) {
  console.log(AJUDA.trim());
  envioInicial.finally(() => process.exit(0));
}

if (modo === "version") {
  console.log(VERSAO);
  envioInicial.finally(() => process.exit(0));
}

// ---------------------------------------------------------------- runner
// Preferência: o runner Node (zero dependência). Se o pacote for antigo/incompleto e só o
// .py estiver presente, cai para o Python — mas isso deixa de ser o caminho normal.
function escolherRunner() {
  if (fs.existsSync(runnerJs)) return { tipo: "node", alvo: runnerJs };
  if (fs.existsSync(runnerPy)) return { tipo: "python", alvo: runnerPy };
  console.error("ai-test-loop: runner não encontrado no pacote (" + path.dirname(runnerJs) + ")");
  process.exit(2);
}

const runner = escolherRunner();

// `--dry-run` e `--show-state` são do runner, não do wrapper: seguem para ele.
if (!saiAntesDoRunner) {
  const candidatos = runner.tipo === "node"
    ? [process.execPath]
    : (process.env.AI_TEST_LOOP_PYTHON ? [process.env.AI_TEST_LOOP_PYTHON] : ["python3", "python"]);

  const inicio = Date.now();
  let saidaScore = "";   // o runner imprime SCORE=<n> na saída
  let saidaGaps = "";

  const tentar = (i) => {
    if (i >= candidatos.length) {
      console.error("ai-test-loop: Python 3 não encontrado (tente AI_TEST_LOOP_PYTHON=/caminho/python3)");
      enviar(Object.assign(base("erro", modo), { python: "ausente" }))
        .finally(() => process.exit(2));
      return;
    }
    const p = spawn(candidatos[i], [runner.alvo, ...argvRunner ?? []], {
      stdio: ["inherit", "pipe", "pipe"],
    });

    // Repassa a saída ao usuário E lê SCORE/GAPS para o evento. Precisa ser pipe: com stdio
    // herdado não daria para ler, e o evento ficaria sem o resultado.
    p.stdout.on("data", (b) => {
      process.stdout.write(b);
      const t = b.toString();
      if (!saidaScore) { const m = t.match(/SCORE=(\d+)/); if (m) saidaScore = m[1]; }
      if (!saidaGaps) { const m = t.match(/GAPS=(\d+)/); if (m) saidaGaps = m[1]; }
    });
    p.stderr.on("data", (b) => process.stderr.write(b));

    p.on("error", () => tentar(i + 1));
    p.on("exit", (code, signal) => {
      enviar(Object.assign(base(code === 0 ? "run" : "erro", modo), {
        duracao_ms: Date.now() - inicio,
        score: saidaScore || undefined,
        gaps: saidaGaps || undefined,
        runtime: runner.tipo,
      })).finally(() => process.exit(signal ? 1 : (code ?? 0)));
    });
  };

  tentar(0);
}
