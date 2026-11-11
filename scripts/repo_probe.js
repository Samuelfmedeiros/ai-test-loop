"use strict";
/**
 * Modo rápido do runner em Node: cole a pasta (ou a URL) do projeto e o teste se
 * monta sozinho. Espelho do `repo_probe.py` — mesma lógica e mesmas regras, porque
 * `npx ai-test-loop` é o caminho de quem NÃO tem Python instalado (medido 11/11/2026:
 * a versão anterior morria com `spawnSync python3 ENOENT`).
 *
 * Duas regras que valem mais que a conveniência:
 *  1. Nunca inventar teste. Só entra checagem cuja ferramenta existe de fato; ferramenta
 *     ausente NÃO vira FAIL (o projeto não falhou, a máquina é que não tem).
 *  2. Nunca escrever dentro do projeto do usuário. Config, estado e relatório vão para
 *     ~/.cache/ai-test-loop/<hash>/.
 */
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");

const SENSITIVE = [".env", ".env.local", ".env.production", "id_rsa", "credentials.json",
  "secrets.yml", "secrets.yaml"];

// Conservadores de propósito: falso positivo aqui assusta sem motivo.
const SECRET_PATTERNS = [
  ["chave da OpenAI/Anthropic", /\b(?:sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9\-_]{20,})/],
  ["chave da AWS", /\bAKIA[0-9A-Z]{16}\b/],
  ["token do GitHub", /\bgh[pousr]_[A-Za-z0-9]{20,}/],
  ["chave privada", /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/],
  ["senha escrita na mão", /\b(?:password|passwd|senha)\s*[:=]\s*["'][^"'\s]{8,}["']/i],
];

const SKIP_DIRS = new Set([".git", "node_modules", "venv", ".venv", "__pycache__", "dist",
  "build", ".next", "target", ".mypy_cache", ".pytest_cache", "coverage", ".tox",
  "site-packages", ".ruff_cache", "vendor"]);

const CODE_EXT = new Set([".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb", ".php",
  ".java", ".cs", ".sh", ".yml", ".yaml", ".json", ".toml", ".env", ".md", ".cfg", ".ini"]);

function cacheDir(seed) {
  const h = crypto.createHash("sha256").update(seed).digest("hex").slice(0, 12);
  const base = process.env.AI_TEST_LOOP_CACHE || path.join(os.homedir(), ".cache", "ai-test-loop", h);
  fs.mkdirSync(base, { recursive: true });
  return base;
}

function walk(root, limitBytes = 400000) {
  const out = [];
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (_) {
      continue;
    }
    for (const ent of entries) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        if (!SKIP_DIRS.has(ent.name) && !ent.name.startsWith(".venv")) stack.push(full);
        continue;
      }
      const ext = path.extname(ent.name).toLowerCase();
      if (ext && !CODE_EXT.has(ext)) continue;
      try {
        if (fs.statSync(full).size > limitBytes) continue;
        out.push([path.relative(root, full), fs.readFileSync(full, "utf8")]);
      } catch (_) {
        /* arquivo ilegível não interrompe a varredura */
      }
    }
  }
  return out;
}

function has(root, ...names) {
  return names.some((n) => fs.existsSync(path.join(root, n)));
}

function gitTracked(root) {
  if (!fs.existsSync(path.join(root, ".git"))) return null;
  const r = spawnSync("git", ["-C", root, "ls-files"], { encoding: "utf8", timeout: 20000 });
  if (r.status !== 0) return null;
  return new Set(r.stdout.split("\n").map((s) => s.trim()).filter(Boolean));
}

function which(bin) {
  const r = spawnSync(process.platform === "win32" ? "where" : "which", [bin],
    { encoding: "utf8" });
  return r.status === 0;
}

function detect(root) {
  const info = { langs: [], pkg: null, scripts: {}, testDir: false };
  if (has(root, "package.json")) {
    info.langs.push("node");
    info.pkg = "npm";
    try {
      const pj = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
      info.scripts = pj.scripts || {};
    } catch (_) { /* package.json inválido não derruba a detecção */ }
    if (has(root, "pnpm-lock.yaml")) info.pkg = "pnpm";
    else if (has(root, "yarn.lock")) info.pkg = "yarn";
  }
  if (has(root, "pyproject.toml", "setup.py", "requirements.txt", "setup.cfg")) info.langs.push("python");
  if (has(root, "go.mod")) info.langs.push("go");
  if (has(root, "Cargo.toml")) info.langs.push("rust");
  info.testDir = ["tests", "test", "__tests__", "spec"].some((d) => fs.existsSync(path.join(root, d)));
  return info;
}

function temTestePy(root) {
  for (const [rel] of walk(root)) {
    const b = path.basename(rel);
    if (b.startsWith("test_") || b.endsWith("_test.py") || b === "conftest.py") return true;
  }
  return false;
}

function checks(root, info) {
  const list = [];
  const faltando = [];
  const langs = new Set(info.langs);

  if (langs.has("python") && temTestePy(root)) {
    list.push({ name: "testes automáticos (Python)", cmd: "python3 -m pytest -q",
      weight: 40, timeout_s: 600, cwd: root });
  }
  if (langs.has("node") && info.scripts.test) {
    const pm = info.pkg || "npm";
    list.push({ name: `testes automáticos (${pm})`,
      cmd: pm === "npm" ? "npm test --silent" : `${pm} test`,
      weight: 40, timeout_s: 600, cwd: root });
  }
  if (langs.has("go") && which("go")) {
    list.push({ name: "testes automáticos (Go)", cmd: "go test ./...",
      weight: 40, timeout_s: 600, cwd: root });
  }
  if (langs.has("rust") && which("cargo")) {
    list.push({ name: "testes automáticos (Rust)", cmd: "cargo test --quiet",
      weight: 40, timeout_s: 900, cwd: root });
  }

  if (langs.has("node") && info.scripts.build) {
    const pm = info.pkg || "npm";
    list.push({ name: "montagem do projeto", cmd: `${pm} run build`,
      weight: 25, timeout_s: 900, cwd: root });
  }

  if (langs.has("python")) {
    if (which("ruff")) {
      list.push({ name: "padrão do código", cmd: "ruff check .",
        weight: 10, timeout_s: 180, cwd: root, required: false });
    } else {
      faltando.push({ nome: "padrão do código", motivo: "ruff não está instalado (pip install ruff)" });
    }
  }
  if (langs.has("node") && info.scripts.lint) {
    const pm = info.pkg || "npm";
    list.push({ name: "padrão do código", cmd: `${pm} run lint`,
      weight: 10, timeout_s: 300, cwd: root, required: false });
  }
  return [list, faltando];
}

function inspect(root) {
  const achados = { segredos: [], caminhos: [], envVersionado: [], readme: false,
    licenca: false, gitignoreEnv: false, arquivos: 0 };

  for (const [rel, texto] of walk(root)) {
    achados.arquivos += 1;
    for (const [rotulo, pat] of SECRET_PATTERNS) {
      const m = texto.match(pat);
      if (m) {
        achados.segredos.push({ arquivo: rel, tipo: rotulo, trecho: m[0].slice(0, 40) });
        break;
      }
    }
    const m2 = texto.match(/(?:\/home\/[a-z0-9_.-]+\/|\/Users\/[A-Za-z0-9_.-]+\/|[A-Z]:\\\\Users\\\\[^\\\\]+)/);
    if (m2) achados.caminhos.push({ arquivo: rel, trecho: m2[0].slice(0, 40) });
  }

  let nomes = [];
  try { nomes = fs.readdirSync(root).map((n) => n.toLowerCase()); } catch (_) { /* raiz ilegível */ }
  achados.readme = nomes.some((n) => n.startsWith("readme"));
  achados.licenca = nomes.some((n) => n.startsWith("license") || n.startsWith("licence") || n.startsWith("copying"));

  const gi = path.join(root, ".gitignore");
  if (fs.existsSync(gi)) {
    try {
      achados.gitignoreEnv = /^\s*(?:\.env|\*\.env|\.env\*)/m.test(fs.readFileSync(gi, "utf8"));
    } catch (_) { /* .gitignore ilegível */ }
  }

  const tracked = gitTracked(root);
  if (tracked) achados.envVersionado = [...tracked].filter((t) => SENSITIVE.includes(path.basename(t))).sort();
  return achados;
}

function nomeProjeto(root, alvo) {
  const pj = path.join(root, "package.json");
  if (fs.existsSync(pj)) {
    try {
      const n = JSON.parse(fs.readFileSync(pj, "utf8")).name;
      if (n) return String(n);
    } catch (_) { /* segue para a próxima fonte */ }
  }
  const pp = path.join(root, "pyproject.toml");
  if (fs.existsSync(pp)) {
    const m = fs.readFileSync(pp, "utf8").match(/^\s*name\s*=\s*["']([^"']+)/m);
    if (m) return m[1];
  }
  if (alvo && /^(?:https?:\/\/|git@)/.test(alvo)) {
    const ultimo = alvo.replace(/\/$/, "").split("/").pop().replace(/\.git$/, "");
    if (ultimo && ultimo !== "repo") return ultimo;
  }
  return path.basename(root.replace(/\/$/, "")) || "projeto";
}

function buildConfig(root, alvo) {
  const info = detect(root);
  const [list, faltando] = checks(root, info);
  const achados = inspect(root);
  return [{ name: nomeProjeto(root, alvo), threshold: 85, max_rounds: 1, checks: list,
    modo_rapido: true }, faltando, achados];
}

const DEP_ERROR = /No module named ['"]?(?:\.?)([\w.]+)/;

function reclassify(results) {
  const mantidos = [];
  const faltando = [];
  for (const r of results) {
    if (!r.passed) {
      if (r.exit_code === 126 || r.exit_code === 127) {
        const linha = (r.evidence || "").trim().split("\n")[0] || r.name;
        faltando.push({ nome: r.name,
          motivo: `o comando necessário não está instalado nesta máquina (${linha.slice(0, 70)})` });
        continue;
      }
      const m = (r.evidence || "").match(DEP_ERROR);
      if (m) {
        const mod = m[1].split(".")[0];
        faltando.push({ nome: r.name,
          motivo: `o projeto precisa de '${mod}', que não está instalado neste ambiente (pip install ${mod})` });
        continue;
      }
    }
    mantidos.push(r);
  }
  return [mantidos, faltando];
}

const RODOU = /\b(\d+)\s+passed\b|\b\d+\s+tests?\s+passed\b/;

// "Não rodou nada" != "rodou e reprovou". Medido 11/11/2026 com `requests`: 407
// testes passaram e 7 falharam — inconclusivo ali esconderia falha real.
function rodouDeVerdade(results) {
  return results.some((r) => {
    const m = (r.evidence || "").match(RODOU);
    return m && Number.parseInt(m[1], 10) > 0;
  });
}

function inconclusive(results) {
  if (results.some((r) => r.passed && r.required !== false)) return false;
  return !rodouDeVerdade(results);
}

function humanReport(cfg, faltando, achados, score, gate, results) {
  const L = [];
  const nome = cfg.name;
  L.push("");
  L.push(`  Resultado de ${nome}`);
  L.push("  " + "-".repeat(Math.max(28, nome.length + 14)));

  const ruins = results.filter((r) => !r.passed && r.required !== false);
  const macios = results.filter((r) => !r.passed && r.required === false);
  const substancial = results.some((r) => r.passed && r.required !== false);

  if (!substancial && faltando.length && !ruins.length) {
    L.push("  Não deu para julgar o projeto: nada chegou a rodar.");
    L.push("");
    L.push("  O que falta instalar:");
    for (const f of faltando) L.push(`    - ${f.nome}: ${f.motivo}`);
    L.push("");
    L.push("  Instale o que falta e rode de novo. Isto NÃO é uma nota baixa.");
    L.push("");
    return L.join("\n");
  }

  if (ruins.length) {
    L.push(`  ${ruins.length} coisa(s) para consertar:`);
    for (const r of ruins) {
      const linhas = (r.evidence || "").trim().split("\n");
      const ultimo = linhas.length ? linhas[linhas.length - 1].slice(0, 110) : "";
      L.push(`    - ${r.name}: ${ultimo}`);
    }
  } else if (macios.length) {
    L.push("  Nenhuma falha grave, mas a nota ficou abaixo do corte por causa dos avisos.");
  } else {
    L.push(faltando.length ? "  Nenhuma falha grave, mas nem tudo pôde ser verificado."
      : "  Nenhum problema encontrado.");
  }

  if (macios.length) {
    L.push("");
    L.push("  Avisos (contam menos, mas derrubam a nota):");
    for (const r of macios) {
      const ev = (r.evidence || "").trim().split("\n");
      L.push(`    - ${r.name}` + (ev.length ? `: ${ev[ev.length - 1].slice(0, 90)}` : ""));
    }
  }

  L.push("");
  const parcial = faltando.length ? " (parcial)" : "";
  const veredito = gate
    ? (faltando.length ? "— passou no que deu para checar." : "— passou.")
    : `— precisa de ${cfg.threshold} para passar.`;
  L.push(`  Nota${parcial}: ${Math.round(score)} de 100 ${veredito}`);

  const alertas = [];
  if (achados.segredos && achados.segredos.length) {
    alertas.push(`tem senha escrita à mão em ${[...new Set(achados.segredos.map((a) => a.arquivo))].slice(0, 3).join(", ")}`);
  }
  if (achados.envVersionado && achados.envVersionado.length) {
    alertas.push(`arquivo de senhas pode ser publicado: ${achados.envVersionado.slice(0, 2).join(", ")}`);
  }
  if (alertas.length) L.push(`  Atenção: ${alertas.join("; ")}.`);

  if (faltando.length) {
    L.push("");
    L.push("  Não deu para checar (e por isso não conta como falha):");
    for (const f of faltando) L.push(`    - ${f.nome}: ${f.motivo}`);
  }
  L.push("");
  return L.join("\n");
}

function prepareRepo(target) {
  if (!/^(?:https?:\/\/|git@)/.test(target)) {
    const p = path.resolve(target.replace(/^~/, os.homedir()));
    if (!fs.existsSync(p) || !fs.statSync(p).isDirectory()) {
      process.stderr.write(`ai-test-loop: não achei a pasta ${p}\n`);
      process.exit(2);
    }
    return p;
  }
  if (!which("git")) {
    process.stderr.write("ai-test-loop: para usar URL eu preciso do git instalado\n");
    process.exit(2);
  }
  const dest = path.join(cacheDir(target), "repo");
  fs.rmSync(dest, { recursive: true, force: true });
  process.stderr.write(`baixando ${target} ...\n`);
  const r = spawnSync("git", ["clone", "--depth", "1", "--quiet", target, dest], { encoding: "utf8" });
  if (r.status !== 0) {
    process.stderr.write(`ai-test-loop: não consegui baixar: ${(r.stderr || "").trim().slice(0, 200)}\n`);
    process.exit(2);
  }
  return dest;
}

module.exports = { buildConfig, cacheDir, prepareRepo, reclassify, inconclusive, humanReport };
