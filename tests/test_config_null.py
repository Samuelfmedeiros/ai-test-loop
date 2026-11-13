"""Contrato: chave com valor `null` no config nao pode virar traceback nem aprovar tudo.

Furo medido em 13/11/2026 (exploit, antes da cura):

  threshold: null  -> Python: `TypeError: float() argument must be ... not 'NoneType'`
                      (exit 1 com traceback, sem dizer qual chave)
                   -> Node:   `Number(null) === 0` => score >= 0 => GATE=passed
                      em QUALQUER pontuacao (pior: aprovava tudo)

`cfg.setdefault("threshold", 85)` nao substitui chave PRESENTE com valor nulo —
so a ausencia. Por isso o default nunca era aplicado.

Rodar: python -m pytest tests/test_config_null.py -q
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

RUNNERS = [
    pytest.param([sys.executable, str(PY_RUNNER)], id="python"),
    pytest.param(["node", str(JS_RUNNER)], marks=pytest.mark.skipif(
        not TEM_NODE, reason="node ausente"), id="node"),
]


def _run(runner, tmp_path, cfg):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    state = tmp_path / ".c-state.json"
    if state.exists():
        state.unlink()
    return subprocess.run([*runner, "--config", str(p)],
                          capture_output=True, text=True, cwd=str(tmp_path))


@pytest.mark.parametrize("runner", RUNNERS)
def test_threshold_null_nao_estoura(runner, tmp_path):
    """null vira o default 85: sem traceback, sem aprovar por 0."""
    r = _run(runner, tmp_path, {"name": "N", "threshold": None,
                                "checks": [{"name": "ok", "cmd": "true",
                                            "weight": 100}]})
    saida = r.stdout + r.stderr
    assert "TypeError" not in saida, saida
    assert "Traceback" not in saida, saida
    assert "THRESHOLD=85" in saida, saida
    assert r.returncode == 0


@pytest.mark.parametrize("runner", RUNNERS)
def test_threshold_null_com_falha_reprova(runner, tmp_path):
    """O bug do Node: Number(null)=0 aprovava qualquer score. Com a cura,
    check que falha PESA e reprova contra o default 85."""
    r = _run(runner, tmp_path, {"name": "N", "threshold": None,
                                "checks": [{"name": "quebrado", "cmd": "false",
                                            "weight": 100}]})
    saida = r.stdout + r.stderr
    assert "GATE=passed" not in saida, saida
    assert r.returncode != 0


@pytest.mark.parametrize("runner", RUNNERS)
def test_max_rounds_null_usa_default(runner, tmp_path):
    """Mesma classe de furo em `max_rounds`."""
    r = _run(runner, tmp_path, {"name": "N", "max_rounds": None,
                                "checks": [{"name": "ok", "cmd": "true",
                                            "weight": 100}]})
    saida = r.stdout + r.stderr
    assert "TypeError" not in saida, saida
    assert "round 1/8" in saida, saida
