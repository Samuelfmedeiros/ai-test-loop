"""Regressao do orcamento de rodadas e da regua congelada.

Dois furos medidos em 12/11/2026 no loop_runner:

1. `max_rounds` era burlavel: `round_no = args.round` sem validar
   monotonicidade. Chamar `--round 1` dez vezes enchia o state de rodadas 1 e
   `round_no > max_rounds` nunca disparava — o orcamento de tentativas era
   decorativo.

2. Nada amarrava o veredito a regua: a config podia ser reescrita entre rodadas
   (apagar o check de seguranca que falhou, baixar o peso, derrubar o
   threshold) e o relatorio nao registrava. Comparar dois rounds era comparar
   reguas diferentes.

Contrato fixado aqui:
  - o round SEMPRE avanca (state manda; --round so empurra para frente)
  - round 1 grava `config_sha256`; rodada seguinte com config diferente marca
    `config_drift: true` com o detalhe esperado/atual

Rodar: python3 -m pytest tests/test_round_budget_and_drift.py -q
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PY_RUNNER = REPO / "scripts" / "loop_runner.py"
JS_RUNNER = REPO / "scripts" / "loop_runner.js"
TEM_NODE = shutil.which("node") is not None

CFG_COM_SEG = {
    "name": "T", "threshold": 90, "max_rounds": 3,
    "checks": [
        {"name": "seguranca", "cmd": "false", "weight": 5, "severity": "block"},
        {"name": "lint", "cmd": "true", "weight": 95},
    ],
}
CFG_SEM_SEG = {
    "name": "T", "threshold": 90, "max_rounds": 3,
    "checks": [{"name": "lint", "cmd": "true", "weight": 95}],
}


def _runner(cmd, tmp_path, cfg, nome):
    p = tmp_path / f"{nome}.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return subprocess.run([*cmd, "--config", str(p)], capture_output=True,
                          text=True, cwd=str(tmp_path))


def _ler(tmp_path, nome):
    return json.loads((tmp_path / nome).read_text(encoding="utf-8"))


@pytest.mark.parametrize("runner", [
    [sys.executable, str(PY_RUNNER)],
    pytest.param(["node", str(JS_RUNNER)], marks=pytest.mark.skipif(
        not TEM_NODE, reason="node ausente")),
], ids=["python", "node"])
def test_round_avanca_e_teto_dispara(runner, tmp_path):
    """O exploit: 5 chamadas com --round 1 num teto de 3."""
    teto = 0
    for _ in range(5):
        r = _runner(runner, tmp_path, CFG_COM_SEG, "rb")
        if "exceeded max_rounds" in r.stdout:
            teto += 1
    st = _ler(tmp_path, ".rb-state.json")
    assert [x["round"] for x in st["rounds"]] == [1, 2, 3], st["rounds"]
    assert teto == 2, f"esperava 2 chamadas abortadas (4a e 5a), veio {teto}"


@pytest.mark.parametrize("runner", [
    [sys.executable, str(PY_RUNNER)],
    pytest.param(["node", str(JS_RUNNER)], marks=pytest.mark.skipif(
        not TEM_NODE, reason="node ausente")),
], ids=["python", "node"])
def test_config_drift_detectado(runner, tmp_path):
    """O ataque real: apagar o check de seguranca entre as rodadas."""
    _runner(runner, tmp_path, CFG_COM_SEG, "dr")
    r1 = _ler(tmp_path, "dr-report-r1.json")
    assert r1["config_drift"] is False
    assert len(r1["config_sha256"]) == 64

    cfg2 = dict(CFG_SEM_SEG, max_rounds=3)
    _runner(runner, tmp_path, cfg2, "dr")
    r2 = _ler(tmp_path, "dr-report-r2.json")
    assert r2["config_drift"] is True
    assert r2["config_drift_detail"]["expected"] == r1["config_sha256"]
    assert r2["config_drift_detail"]["actual"] == r2["config_sha256"]


@pytest.mark.parametrize("runner", [
    [sys.executable, str(PY_RUNNER)],
    pytest.param(["node", str(JS_RUNNER)], marks=pytest.mark.skipif(
        not TEM_NODE, reason="node ausente")),
], ids=["python", "node"])
def test_config_igual_nao_marca_drift(runner, tmp_path):
    """Ruido zero: mesma config duas vezes nao pode alarmar."""
    _runner(runner, tmp_path, CFG_COM_SEG, "ok")
    _runner(runner, tmp_path, CFG_COM_SEG, "ok")
    r2 = _ler(tmp_path, "ok-report-r2.json")
    assert r2["config_drift"] is False
