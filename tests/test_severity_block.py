"""Regressao do bloqueio duro por severidade (severity: "block").

Contexto medido em 12/11/2026: o gate era puramente numerico
(`gate = value >= threshold`). Um check OBRIGATORIO de seguranca falhando com
peso 5 num total de 100 dava SCORE=95 com threshold 90 -> GATE=passed, exit 0.
O README prometia o contrario ("a secret in the diff... blocks delivery even
with a perfect score").

Este teste fixa as duas metades do contrato:
  1. severity="block" falhando  -> GATE=blocked, exit 3, mesmo com score alto.
  2. severity ausente           -> comportamento anterior intacto (exit 0).

Rodar: python3 -m pytest tests/test_severity_block.py -q
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PY_RUNNER = REPO / "scripts" / "loop_runner.py"
JS_RUNNER = REPO / "scripts" / "loop_runner.js"

CHECKS_FAIL_BLOCK = [
    {"name": "seguranca", "cmd": "false", "weight": 5, "severity": "block"},
    {"name": "lint", "cmd": "true", "weight": 95},
]
CHECKS_NO_SEVERITY = [
    {"name": "seguranca", "cmd": "false", "weight": 5},
    {"name": "lint", "cmd": "true", "weight": 95},
]


def _rodar(runner, checks, tmp_path, nome):
    cfg = {"threshold": 90, "max_rounds": 3, "checks": checks}
    cfg_path = tmp_path / f"{nome}.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    return subprocess.run(
        [*runner, "--config", str(cfg_path)],
        capture_output=True, text=True, cwd=str(tmp_path),
    )


def test_block_falhando_bloqueia_mesmo_com_score_alto(tmp_path):
    """O exploit: score 95 >= threshold 90, mas o check block falhou."""
    r = _rodar([sys.executable, str(PY_RUNNER)], CHECKS_FAIL_BLOCK, tmp_path, "blk")
    assert "SCORE=95.0" in r.stdout, r.stdout + r.stderr
    assert "GATE=blocked" in r.stdout, r.stdout + r.stderr
    assert "seguranca" in r.stdout
    assert r.returncode == 3, f"exit={r.returncode} (esperado 3)"


def test_sem_severity_preserva_comportamento_anterior(tmp_path):
    """Compatibilidade: config antiga (sem severity) nao muda de veredito."""
    r = _rodar([sys.executable, str(PY_RUNNER)], CHECKS_NO_SEVERITY, tmp_path, "noblk")
    assert "GATE=passed" in r.stdout, r.stdout + r.stderr
    assert r.returncode == 0


def test_relatorio_registra_os_blockers(tmp_path):
    r = _rodar([sys.executable, str(PY_RUNNER)], CHECKS_FAIL_BLOCK, tmp_path, "rep")
    # O nome do relatorio deriva do nome do CONFIG (rep.json -> rep-report-r1.json),
    # nao do campo "project". Medido 12/11/2026.
    rep = json.loads((tmp_path / "rep-report-r1.json").read_text(encoding="utf-8"))
    assert rep["blocked"] is True
    assert rep["blocking"] == ["seguranca"]


@pytest.mark.skipif(
    subprocess.run(["which", "node"], capture_output=True).returncode != 0,
    reason="node ausente",
)
def test_js_tem_paridade(tmp_path):
    """Os dois runners precisam concordar, senao metade dos usuarios nao tem gate."""
    r = _rodar(["node", str(JS_RUNNER)], CHECKS_FAIL_BLOCK, tmp_path, "js")
    assert "GATE=blocked" in r.stdout, r.stdout + r.stderr
    assert r.returncode == 3
