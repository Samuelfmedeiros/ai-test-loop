#!/usr/bin/env bash
# publica-py: builda e publica ai-test-loop no PyPI com o pyproject CANONICO.
#
# Motivo (medido 14/11/2026):
# O pyproject.toml versionado no repo e' o do ESPELHO `agent-test-loop`
# (name = "agent-test-loop", packages = [], depende do canonico). O pacote
# canonico `ai-test-loop` foi publicado (1.1.0, 1.1.2, 1.1.3) com o pyproject
# trocado na mao e restaurado depois — NUNCA commitado. Resultado: o repo nao
# consegue reproduzir o que esta no PyPI, e quem clonar publica o espelho.
#
# Este script escreve o pyproject canonico num diretorio de build separado,
# deixando o repo intocado. Nada de sed no pyproject versionado.
#
# Uso:
#   ./scripts/publica-py.sh 1.2.3          # build + publish
#   ./scripts/publica-py.sh 1.2.3 --dry    # so build e mostra os arquivos
set -euo pipefail

VERSAO="${1:?uso: publica-py.sh <versao> [--dry]}"
DRY="${2:-}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${REPO}/dist-py"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[publica-py] repo=$REPO versao=$VERSAO"

# 1. Copia o que vai no pacote
cp -r "$REPO/ai_test_loop" "$WORK/"
cp -r "$REPO/scripts" "$WORK/" 2>/dev/null || true
cp -r "$REPO/data" "$WORK/" 2>/dev/null || true
cp "$REPO/README.md" "$REPO/LICENSE" "$REPO/MANIFEST.in" "$WORK/"
find "$WORK" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$WORK" -name '*.bak-*' -delete 2>/dev/null || true
find "$WORK" -name '*.old-*' -delete 2>/dev/null || true

# 2. Escreve o pyproject CANONICO (o que o PyPI realmente tem)
cat > "$WORK/pyproject.toml" <<'PYEOF'
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "ai-test-loop"
version = "__VERSAO__"
description = "Deterministic critic for AI agents: runs weighted checks for real, scores 0-100 and blocks the delivery when it should."
readme = "README.md"
requires-python = ">=3.9"
license = { text = "MIT" }
authors = [{ name = "Samuel Medeiros" }]
keywords = [
    "mcp", "ai-agent", "ai-agents", "llm", "testing", "test-loop",
    "quality-gate", "delivery-gate", "critic", "claude-code", "cursor", "code-review",
]
classifiers = [
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Intended Audience :: Developers",
    "Topic :: Software Development :: Quality Assurance",
    "Topic :: Software Development :: Testing",
]

[project.urls]
Homepage = "https://skills.seu.pet/p/ai-test-loop?utm_source=pypi&utm_medium=readme&utm_campaign=ai-test-loop"
Repository = "https://github.com/Samuelfmedeiros/ai-test-loop"
Changelog = "https://github.com/Samuelfmedeiros/ai-test-loop/releases"

[project.scripts]
ai-test-loop = "ai_test_loop.__main__:main"
agent-test-loop = "ai_test_loop.__main__:main"

[tool.setuptools.packages.find]
include = ["ai_test_loop*"]
PYEOF
sed -i "s/__VERSAO__/${VERSAO}/" "$WORK/pyproject.toml"

# 3. Sincroniza os runners que o pacote EMBUTE (ai_test_loop/scripts/).
# Medido 14/11/2026: ai_test_loop/__main__.py faz exec do runner em
# ai_test_loop/scripts/loop_runner.py — a copia interna, nao o scripts/ do repo.
# A copia interna estava defasada (md5 eab80b94 vs 4dbc87c3) e SEM o fix do
# KeyError que esta em scripts/repo_probe.py. Publicar sem sincronizar entrega
# codigo velho com numero de versao novo.
mkdir -p "$WORK/ai_test_loop/scripts"
# Todos os modulos que o runner importa. Medido 14/11/2026: loop_runner.py faz
# `from gate_core import ...` e gate_core.py NAO estava no pacote — o comando
# instalado morria com ModuleNotFoundError na primeira execucao. A 1.1.3 do PyPI
# nao tinha o problema porque o import nasceu na 1.2.0.
for f in "$REPO"/scripts/*.py; do
  base="$(basename "$f")"
  cp "$f" "$WORK/ai_test_loop/scripts/$base"
  echo "[publica-py] embutido: $base (md5 $(md5sum "$f" | cut -c1-8))"
done

# 4. Versao no __init__ do pacote (fonte unica de verdade do runtime)
sed -i "s/^__version__ = .*/__version__ = \"${VERSAO}\"/" "$WORK/ai_test_loop/__init__.py"

echo "[publica-py] pyproject canonico:"
grep -E '^(name|version) = ' "$WORK/pyproject.toml"
grep -m1 '__version__' "$WORK/ai_test_loop/__init__.py"

# 4. Build num venv efemero (a maquina nao tem build/twine instalados)
mkdir -p "$OUT"
rm -f "$OUT"/ai_test_loop-*.whl "$OUT"/ai_test_loop-*.tar.gz
(cd "$WORK" && uv venv --quiet .venv >/dev/null 2>&1 && \
  uv pip install --quiet --python .venv/bin/python build twine >/dev/null 2>&1 && \
  .venv/bin/python -m build --sdist --wheel --outdir "$OUT" 2>&1 | tail -4)

echo "[publica-py] artefatos:"
ls -l "$OUT"/ai_test_loop-"$VERSAO"* 2>/dev/null || { echo "FALHA: nada buildado"; exit 1; }

if [ "$DRY" = "--dry" ]; then
  echo "[publica-py] --dry: parando antes do upload"
  exit 0
fi

# 5. Publica com o token do Bitwarden (nunca em argv, o twine le do env)
export BWS_ACCESS_TOKEN="$(grep -h '^BWS_ACCESS_TOKEN' "$HOME/.hermes/.env" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '"')"
TWINE_PASSWORD="$(~/.hermes/tools/bws-2.0.0-linux-x64/bws secret get 58293b77-f116-416d-969f-b4ed0171d100 2>/dev/null \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["value"])')"
[ -n "$TWINE_PASSWORD" ] || { echo "FALHA: token PYPI_GERAL vazio no Bitwarden"; exit 1; }
export TWINE_USERNAME=__token__
export TWINE_PASSWORD

(cd "$WORK" && .venv/bin/python -m twine upload --non-interactive --skip-existing "$OUT"/ai_test_loop-"$VERSAO"* 2>&1 | tail -6)

echo "[publica-py] conferindo no PyPI:"
curl -s --max-time 30 https://pypi.org/pypi/ai-test-loop/json \
  | python3 -c 'import sys,json;print("PyPI latest =", json.load(sys.stdin)["info"]["version"])'
