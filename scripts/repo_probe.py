"""Modo rápido: cole o caminho (ou a URL) do seu projeto e o teste se monta sozinho.

O problema medido em 11/11/2026: o runner exige um `loop.json` escrito à mão. Quem
chega querendo "ver se meu projeto está bom" tem que descobrir o schema, escolher
comandos, pesar notas — e desiste antes de rodar. Este módulo elimina o `loop.json`
do caminho: olha o projeto, descobre o que dá para checar e roda.

Duas regras que valem mais que a conveniência:

1. **Nunca inventar um teste.** Uma checagem só entra na lista se a ferramenta dela
   existe de fato na máquina e o projeto tem aquele arquivo. Ferramenta ausente não
   vira FAIL (seria mentira: o projeto não falhou, ela é que não está instalada) —
   vira uma linha em "não deu para checar", dita com o motivo.

2. **Nunca escrever dentro do projeto do usuário.** Config, estado e relatório vão
   para `~/.cache/ai-test-loop/<hash-do-caminho>/`. Quem colou um projeto para
   testar não espera encontrar lixo nosso no git dele.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

# Arquivos que, se existirem versionados, são vazamento de senha/segredo
SENSITIVE = (".env", ".env.local", ".env.production", "id_rsa", "credentials.json",
             "secrets.yml", "secrets.yaml")

# Padrões que parecem credencial de verdade. Conservadores de propósito: falso
# positivo aqui assusta o usuário sem motivo, e o custo de ignorar um é menor que
# o de gritar lobo em toda rodada.
SECRET_PATTERNS = (
    ("chave da OpenAI/Anthropic", r"\b(?:sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9\-_]{20,})"),
    ("chave da AWS", r"\bAKIA[0-9A-Z]{16}\b"),
    ("token do GitHub", r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    ("chave privada", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("senha escrita na mão", r"""(?i)\b(?:password|passwd|senha)\s*[:=]\s*["'][^"'\s]{8,}["']"""),
)

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
             ".next", "target", ".mypy_cache", ".pytest_cache", "coverage", ".tox",
             "site-packages", ".ruff_cache", "vendor"}

# arquivos de código que interessam para varredura de segredo e de caminho pessoal
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb", ".php", ".java",
            ".cs", ".sh", ".yml", ".yaml", ".json", ".toml", ".env", ".md", ".cfg", ".ini"}


def _walk(root: str, limit_bytes: int = 400_000):
    """Percorre o projeto devolvendo (caminho_relativo, texto) só de arquivos legíveis.

    Ignora as pastas gigantes de dependência: varrer `node_modules` num projeto node
    levaria minutos e acusaria segredo dentro de biblioteca de terceiro, que não é
    problema do usuário.
    """
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".venv")]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext and ext not in CODE_EXT:
                continue
            full = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(full) > limit_bytes:
                    continue
                with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                    yield os.path.relpath(full, root), fh.read()
            except OSError:
                continue


def _has(root: str, *names: str) -> bool:
    return any(os.path.exists(os.path.join(root, n)) for n in names)


def _modulo_py(nome: str) -> bool:
    """True só se o módulo importa neste interpretador — não adivinha por 'which'."""
    try:
        out = subprocess.run([sys.executable, "-c", f"import {nome}"],
                             capture_output=True, timeout=20)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _git_tracked(root: str) -> set[str] | None:
    """Arquivos versionados, ou None quando não é repo git / git não existe."""
    if not shutil.which("git") or not os.path.isdir(os.path.join(root, ".git")):
        return None
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}


def _detect(root: str) -> dict:
    """O que é este projeto, na prática: linguagem, gerenciador e scripts declarados."""
    info: dict = {"langs": [], "pkg": None, "scripts": {}, "test_dir": False}

    if _has(root, "package.json"):
        info["langs"].append("node")
        info["pkg"] = "npm"
        try:
            pj = json.loads(pathlib.Path(root, "package.json").read_text(encoding="utf-8"))
            info["scripts"] = pj.get("scripts") or {}
        except (OSError, json.JSONDecodeError):
            pass
        if _has(root, "pnpm-lock.yaml"):
            info["pkg"] = "pnpm"
        elif _has(root, "yarn.lock"):
            info["pkg"] = "yarn"

    if _has(root, "pyproject.toml", "setup.py", "requirements.txt", "setup.cfg"):
        info["langs"].append("python")
    if _has(root, "go.mod"):
        info["langs"].append("go")
    if _has(root, "Cargo.toml"):
        info["langs"].append("rust")

    info["test_dir"] = any(os.path.isdir(os.path.join(root, d))
                           for d in ("tests", "test", "__tests__", "spec"))
    return info


def _checks(root: str, info: dict) -> tuple[list[dict], list[dict]]:
    """Monta as checagens possíveis.

    Devolve (checks, nao_checado). A segunda lista é a parte honesta: o que o projeto
    tem e daria para checar, mas a ferramenta não está instalada nesta máquina.
    """
    checks: list[dict] = []
    faltando: list[dict] = []
    langs = set(info["langs"])

    # ---- testes automatizados
    if "python" in langs:
        def _tem_teste_py() -> bool:
            if info["test_dir"]:
                return True
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                if any(fn.startswith("test_") or fn.endswith("_test.py") or fn == "conftest.py"
                       for fn in filenames):
                    return True
            return False

        if _tem_teste_py():
            checks.append({"name": "testes automáticos (Python)",
                           "cmd": f"{sys.executable} -m pytest -q",
                           "weight": 40, "timeout_s": 600, "cwd": root})
    if "node" in langs and info["scripts"].get("test"):
        pm = info["pkg"] or "npm"
        checks.append({"name": f"testes automáticos ({pm})",
                       "cmd": f"{pm} test --silent" if pm == "npm" else f"{pm} test",
                       "weight": 40, "timeout_s": 600, "cwd": root})
    if "go" in langs and shutil.which("go"):
        checks.append({"name": "testes automáticos (Go)", "cmd": "go test ./...",
                       "weight": 40, "timeout_s": 600, "cwd": root})
    if "rust" in langs and shutil.which("cargo"):
        checks.append({"name": "testes automáticos (Rust)", "cmd": "cargo test --quiet",
                       "weight": 40, "timeout_s": 900, "cwd": root})

    # ---- montagem/compilação
    if "node" in langs and info["scripts"].get("build"):
        pm = info["pkg"] or "npm"
        checks.append({"name": "montagem do projeto", "cmd": f"{pm} run build",
                       "weight": 25, "timeout_s": 900, "cwd": root})
    if "python" in langs and _has(root, "pyproject.toml", "setup.py"):
        if _modulo_py("build"):
            checks.append({"name": "o pacote se monta",
                           "cmd": f"{sys.executable} -m build --wheel --outdir {cache_dir(root)}/dist",
                           "weight": 15, "timeout_s": 300, "cwd": root, "required": False})
        else:
            faltando.append({"nome": "o pacote se monta",
                             "motivo": "pacote 'build' não instalado (pip install build)"})

    # ---- padrão de código (só quando a ferramenta existe de verdade)
    if "python" in langs:
        if shutil.which("ruff"):
            checks.append({"name": "padrão do código", "cmd": "ruff check .",
                           "weight": 10, "timeout_s": 180, "cwd": root, "required": False})
        else:
            faltando.append({"nome": "padrão do código",
                             "motivo": "ruff não está instalado (pip install ruff)"})
    if "node" in langs and info["scripts"].get("lint"):
        pm = info["pkg"] or "npm"
        checks.append({"name": "padrão do código", "cmd": f"{pm} run lint",
                       "weight": 10, "timeout_s": 300, "cwd": root, "required": False})

    return checks, faltando


def inspect(root: str) -> dict:
    """Varredura própria, sem dependência: segredo, caminho pessoal, docs e senha no git."""
    achados = {"segredos": [], "caminhos": [], "env_versionado": [], "readme": False,
               "licenca": False, "gitignore_env": False, "arquivos": 0}

    tracked = _git_tracked(root)
    for rel, texto in _walk(root):
        achados["arquivos"] += 1
        for rotulo, pat in SECRET_PATTERNS:
            m = re.search(pat, texto)
            if m:
                achados["segredos"].append({"arquivo": rel, "tipo": rotulo,
                                            "trecho": m.group(0)[:40]})
                break
        # caminho absoluto de usuário vaza nome de conta e estrutura da máquina
        m = re.search(r"(?:/home/[a-z0-9_.-]+/|/Users/[A-Za-z0-9_.-]+/|[A-Z]:\\\\Users\\\\[^\\\\]+)",
                      texto)
        if m:
            achados["caminhos"].append({"arquivo": rel, "trecho": m.group(0)[:40]})

    nomes = {n.lower() for n in os.listdir(root)} if os.path.isdir(root) else set()
    achados["readme"] = any(n.startswith("readme") for n in nomes)
    achados["licenca"] = any(n.startswith(("license", "licence", "copying")) for n in nomes)

    gi = os.path.join(root, ".gitignore")
    if os.path.exists(gi):
        try:
            corpo = pathlib.Path(gi).read_text(encoding="utf-8", errors="ignore")
            achados["gitignore_env"] = bool(re.search(r"^\s*(?:\.env|\*\.env|\.env\*)", corpo, re.M))
        except OSError:
            pass

    if tracked is not None:
        achados["env_versionado"] = sorted(t for t in tracked
                                           if os.path.basename(t) in SENSITIVE)
    return achados


def _nome_projeto(root: str, alvo: str = "") -> str:
    """Nome legível do projeto: declarado > nome da URL > nome da pasta."""
    for arq, campo in (("package.json", "name"), ("pyproject.toml", "name")):
        caminho = os.path.join(root, arq)
        if not os.path.exists(caminho):
            continue
        try:
            texto = pathlib.Path(caminho).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if arq.endswith(".json"):
            try:
                v = json.loads(texto).get(campo)
            except json.JSONDecodeError:
                v = None
            if v:
                return str(v)
        else:
            m = re.search(r'^\s*name\s*=\s*["\']([^"\']+)', texto, re.M)
            if m:
                return m.group(1)
    if alvo and re.match(r"^(?:https?://|git@)", alvo):
        # https://github.com/psf/requests(.git) -> requests
        ultimo = re.sub(r"\.git$", "", alvo.rstrip("/").split("/")[-1])
        if ultimo and ultimo != "repo":
            return ultimo
    return os.path.basename(root.rstrip("/")) or "projeto"


def build_config(root: str, alvo: str = "") -> tuple[dict, list[dict], dict]:
    """Config pronto para o runner + o que não deu para checar."""
    root = os.path.abspath(root)
    info = _detect(root)
    checks, faltando = _checks(root, info)
    achados = inspect(root)
    pname = _nome_projeto(root, alvo)

    if not checks:
        # sem suíte de teste detectável o projeto não fica sem nota: as checagens
        # estruturais abaixo valem por si e apontam o que falta de verdade.
        pass

    cfg = {"name": pname, "threshold": 85, "max_rounds": 1, "checks": checks,
           "modo_rapido": True}
    return cfg, faltando, achados


def human_report(root: str, cfg: dict, faltando: list[dict], achados: dict,
                 score: float, gate: bool, results: list[dict]) -> str:
    """A mesma informação da saída técnica, em português de gente.

    Quem cola um projeto para "ver se está bom" não é necessariamente programador.
    A saída técnica continua existindo (vai para o relatório e para --json); esta
    é a que responde a pergunta que a pessoa fez.
    """
    nome = cfg["name"]
    L: list[str] = []
    L.append("")
    L.append(f"  Resultado de {nome}")
    L.append("  " + "-" * max(28, len(nome) + 14))

    bons = [r for r in results if r["passed"]]
    ruins = [r for r in results if not r["passed"] and r.get("required", True)]
    macios = [r for r in results if not r["passed"] and not r.get("required", True)]

    abaixo = [r for r in macios if not r["passed"]]
    # "Rodou" = passou uma checagem que vale nota cheia. Lint passar sozinho não é
    # veredito sobre o projeto: dar 100 de 100 porque o ruff não reclamou é o verde
    # falso que este produto existe para caçar (medido 11/11/2026 com `requests`).
    substancial = [r for r in results if r["passed"] and r.get("required", True)]
    nenhum_rodou = not substancial
    if nenhum_rodou and faltando and not ruins:
        # Nada passou E há motivo de ambiente: o projeto não foi julgado. Reprovar
        # aqui é o pior dos mundos — o usuário leva uma nota zero por não ter
        # instalado uma ferramenta. Este bloco diz isso com todas as letras.
        L.append("  Não deu para julgar o projeto: nada chegou a rodar.")
        L.append("")
        L.append("  O que falta instalar:")
        for f in faltando:
            L.append(f"    - {f['nome']}: {f['motivo']}")
        L.append("")
        L.append("  Instale o que falta e rode de novo. Isto NÃO é uma nota baixa.")
        L.append("")
        return "\n".join(L)

    if not ruins and not abaixo:
        L.append("  Nenhuma falha grave, mas nem tudo pôde ser verificado."
                 if faltando else "  Nenhum problema encontrado.")
    elif not ruins and abaixo:
        L.append("  Nenhuma falha grave, mas a nota ficou abaixo do corte por causa dos avisos.")
    else:
        L.append(f"  {len(ruins)} coisa(s) para consertar:")
        for r in ruins:
            motivo = (r["evidence"] or "").strip().splitlines()
            ultimo = motivo[-1][:110] if motivo else ""
            L.append(f"    - {r['name']}: {ultimo}")

    if macios:
        L.append("")
        L.append("  Avisos (contam menos, mas derrubam a nota):")
        for r in macios:
            ev = (r["evidence"] or "").strip().splitlines()
            L.append(f"    - {r['name']}" + (f": {ev[-1][:90]}" if ev else ""))

    L.append("")
    parcial = ""
    if faltando:
        # Nota calculada só sobre o que rodou. Dizer "100 de 100" com o principal
        # sem rodar seria o verde falso que este produto existe para caçar.
        parcial = " (parcial)"
    L.append(f"  Nota{parcial}: {score:.0f} de 100 "
             + ("— passou no que deu para checar."
                if gate and faltando else
                "— passou." if gate else
                f"— precisa de {cfg['threshold']} para passar."))

    # a varredura própria é o que mais conversa com leigo: fala de senha e de doc,
    # não de framework. Só aparece quando tem algo a dizer.
    alertas = []
    if achados["segredos"]:
        nomes = sorted({a["arquivo"] for a in achados["segredos"]})[:3]
        alertas.append(f"tem senha escrita à mão em {', '.join(nomes)}")
    if achados["env_versionado"]:
        alertas.append(f"arquivo de senhas pode ser publicado: {', '.join(achados['env_versionado'][:2])}")
    if alertas:
        L.append("  Atenção: " + "; ".join(alertas) + ".")

    if faltando:
        L.append("")
        L.append("  Não deu para checar (e por isso não conta como falha):")
        for f in faltando:
            L.append(f"    - {f['nome']}: {f['motivo']}")

    L.append("")
    return "\n".join(L)


# Falha por falta de dependência do projeto NÃO é falha do projeto: o pytest não
# conseguiu nem importar o que devia testar. Marcar isso como FAIL acusa o usuário
# por um problema da máquina (medido 11/11/2026: `requests` deu nota 20/100 por
# falta do próprio pacote instalado). Vira "não deu para checar", com o nome do
# módulo e o comando para resolver.
# O Python escreve isso de duas formas: com exceção (`ModuleNotFoundError: No module
# named 'x'`) e seca, quando o próprio módulo que falta é o que roda (`python -m pytest`
# sem pytest instalado imprime só `/usr/bin/python3: No module named pytest`). As duas
# são ambiente, não veredito do projeto — por isso o prefixo da exceção é opcional.
_DEP_ERROR = re.compile(r"No module named ['\"]?(?:\.?)([\w.]+)")


def reclassify(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separa falha real de falta de ambiente. Devolve (mantidos, nao_checados)."""
    mantidos, faltando = [], []
    for r in results:
        if not r["passed"]:
            # exit 127 = comando não encontrado; 126 = existe mas não executa.
            # Nenhum dos dois é veredito sobre o projeto (medido 11/11/2026: `npm`
            # ausente derrubou a nota para 0 e o resumo acusou o usuário).
            if r.get("exit_code") in (126, 127):
                primeira = (r["evidence"] or "").strip().splitlines()
                cmd = (primeira[0][:70] if primeira else r["name"])
                faltando.append({
                    "nome": r["name"],
                    "motivo": f"o comando necessário não está instalado nesta máquina ({cmd})",
                })
                continue
            m = _DEP_ERROR.search(r.get("evidence") or "")
            if m:
                mod = m.group(1).split(".")[0]
                faltando.append({
                    "nome": r["name"],
                    "motivo": f"o projeto precisa de '{mod}', que não está instalado "
                              f"neste ambiente (pip install {mod})",
                })
                continue
        mantidos.append(r)
    return mantidos, faltando


_RODOU = re.compile(r"\b(\d+)\s+passed\b|\b\d+\s+tests?\s+passed\b")


def _rodou_de_verdade(results: list[dict]) -> bool:
    """Alguma checagem executou testes de fato (evidência com contagem de passed)?

    Diferencia "não rodou nada" de "rodou e reprovou". Medido 11/11/2026 com
    `requests`: 407 testes passaram e 7 falharam — chamar isso de inconclusivo
    esconderia uma falha real do projeto.
    """
    for r in results:
        m = _RODOU.search(r.get("evidence") or "")
        if m and int(m.group(1) or 0) > 0:
            return True
    return False


def inconclusive(results: list[dict]) -> bool:
    """True só quando NADA rodou de verdade — nem aprovando, nem reprovando.

    Só faz sentido no modo rápido: ali a lista de checagens foi montada por nós, e
    se nada rodou é porque o ambiente está incompleto — não porque o projeto é ruim.
    Com --config o usuário declarou o que medir e o resultado dele vale.
    """
    if any(r["passed"] and r.get("required", True) for r in results):
        return False
    return not _rodou_de_verdade(results)


def prepare_repo(target: str) -> str:
    """Aceita caminho local ou URL de repositório. Devolve a pasta local."""
    if not re.match(r"^(?:https?://|git@)", target):
        caminho = os.path.abspath(os.path.expanduser(target))
        if not os.path.isdir(caminho):
            sys.stderr.write(f"ai-test-loop: não achei a pasta {caminho}\n")
            raise SystemExit(2)
        return caminho

    if not shutil.which("git"):
        sys.stderr.write("ai-test-loop: para usar URL eu preciso do git instalado\n")
        raise SystemExit(2)
    dest = os.path.join(cache_dir(target), "repo")
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    sys.stderr.write(f"baixando {target} ...\n")
    r = subprocess.run(["git", "clone", "--depth", "1", "--quiet", target, dest],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(f"ai-test-loop: não consegui baixar: {(r.stderr or '').strip()[:200]}\n")
        raise SystemExit(2)
    return dest


def cache_dir(seed: str) -> str:
    """Onde guardo config/estado/relatório — nunca dentro do projeto do usuário."""
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    base = os.environ.get("AI_TEST_LOOP_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "ai-test-loop", h)
    os.makedirs(base, exist_ok=True)
    return base
