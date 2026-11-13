"""Meta-gate (Fase 6 do contrato Roger v4): Roger NAO aprova entrega com defeito semeado.

Regra: "se o Roger aprovar defeito semeado, o contrato nao entra."

O teste:
  1. monta um projeto minimo com um teste que PASSA;
  2. semeia um defeito (@pytest.mark.skip num teste que passava);
  3. roda o loop_runner com `strict_tests: true` + `blocking_gaps`;
  4. espera GATE=failed e exit 1 (entrega NÃO aprovada);
  5. remove o defeito e espera GATE=passed e exit 0 (entrega aprovada).

Se qualquer passo inverter, o contrato esta aprovando defeito e nao pode
ser rolado para os 14 grupos.

Rodar: python3 -m pytest tests/test_meta_gate_seeded_defect.py -q
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# O runner vive no proprio repositorio (nao em ~/.hermes/scripts).
RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "loop_runner.py"
PYTEST_PROJ = """
import pytest


def test_ok():
    assert True


@pytest.mark.skip(reason="DEFEITO SEMEADO — skip nao pode aprovar")
def test_seeded_skip():
    assert False
"""

CLEAN_PROJ = """
def test_ok():
    assert True


def test_outro():
    assert 1 + 1 == 2
"""

CONFIG = {
    "name": "meta",
    "threshold": 50,
    "max_rounds": 2,
    "strict_tests": True,
    "blocking_gaps": ["skip", "sem testes novos"],
    "blocking_gaps": ["sem testes novos", "skip", "zero testes"],
    "checks": [
        {"name": "suite", "cmd": "pytest -q", "weight": 100,
         "allow_skipped": False, "min_passed": 1},
    ],
}


def _run(tmp_path, nome_cfg="loop.meta.json", nome_proj="loop.meta.json"):
    cfg = tmp_path / nome_cfg
    cfg.write_text(json.dumps(CONFIG), encoding="utf-8")
    p = subprocess.run([sys.executable, str(RUNNER), "--config", str(cfg),
                        "--quiet"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    saida = p.stdout + p.stderr
    return p.returncode, saida


def _monta_projeto(tmp_path, conteudo_py):
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_x.py").write_text(conteudo_py, encoding="utf-8")


def test_defeito_semeado_nao_e_aprovado(tmp_path):
    """Defeito semeado = BLOQUEIO DURO (exit 3), nao "nota baixa" (exit 1).

    Contradicao apontada 12/11/2026: §1/§9.4 prometem exit 3 para bloqueio, mas
    o meta-gate assertava exit 1 — verde na assercao fraca. Se `blocking_gaps`
    invalida a entrega, o veredito e blocked/3. Asserção corrigida para 3.
    """
    _monta_projeto(tmp_path, PYTEST_PROJ)
    rc, saida = _run(tmp_path)
    assert rc == 3, f"esperado BLOQUEADO (3), veio {rc}: {saida}"
    assert "GATE=blocked" in saida, saida
    assert "blocking_gap" in saida, saida


def test_entrada_limpa_e_aprovada(tmp_path):
    _monta_projeto(tmp_path, CLEAN_PROJ)
    rc, saida = _run(tmp_path)
    assert rc == 0, f"esperado Aprovado (0), veio {rc}: {saida}"
    assert "GATE=passed" in saida, saida
