"""Contrato anti-cheat e anti-drift do loop_runner (adendo v1.1 do contrato).

Cada teste aqui corresponde a um furo MEDIDO em 12/11/2026, nao a uma hipotese:

1. `required: true` sem `severity` — o autor acreditava ter um bloqueio e criou
   um desconto de peso. Config assim e recusada na carga (fail-closed).
2. `skip` escondido — o agente marca @pytest.mark.skip no caso que falha e a
   suite fica verde. `strict_tests: true` derruba; ausencia nao policia
   (compatibilidade com config antiga).
3. `pytest -q` com warnings NAO emite o sumario ("1 passed, 1 skipped"), so
   `.s [100%]`. O parser cai no fallback de progresso; sem numero reconhecivel
   o resultado e NOT VERIFIABLE, nunca aprovado.
4. config reescrita entre rodadas sem aprovacao — drift invalida o gate
   (exit 3), `--approve <id>` re-baseia e registra no state.

Rodar: python3 -m pytest tests/test_anticheat_e_drift.py -q
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

TESTS_COM_SKIP = '''import pytest

def test_ok():
    assert True

@pytest.mark.skip(reason="comodo")
def test_chave():
    assert False
'''


def _run(runner, tmp_path, cfg, *extra, nome="c"):
    p = tmp_path / f"{nome}.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return subprocess.run([*runner, "--config", str(p), *extra],
                          capture_output=True, text=True, cwd=str(tmp_path))


def _com_skip(tmp_path):
    t = tmp_path / "t"
    t.mkdir(exist_ok=True)
    (t / "test_x.py").write_text(TESTS_COM_SKIP, encoding="utf-8")


@pytest.mark.parametrize("runner", RUNNERS)
def test_required_sem_severity_e_config_invalida(runner, tmp_path):
    """O furo original: required:true prometia bloqueio e nao bloqueava."""
    cfg = {"name": "T", "checks": [
        {"name": "sec", "cmd": "true", "weight": 5, "required": True}]}
    r = _run(runner, tmp_path, cfg)
    assert r.returncode != 0
    assert "required=true sem severity" in (r.stdout + r.stderr)


@pytest.mark.parametrize("runner", RUNNERS)
def test_skip_derruba_com_strict_tests(runner, tmp_path):
    _com_skip(tmp_path)
    cfg = {"name": "T", "threshold": 50, "max_rounds": 3, "strict_tests": True,
           "checks": [{"name": "suite", "cmd": "cd t && python3 -m pytest -q",
                       "weight": 100}]}
    r = _run(runner, tmp_path, cfg)
    saida = r.stdout + r.stderr
    assert "GATE=passed" not in saida, saida
    assert "skipped" in saida
    assert r.returncode == 1


@pytest.mark.parametrize("runner", RUNNERS)
def test_sem_strict_nao_muda_comportamento_antigo(runner, tmp_path):
    """Compatibilidade: config que ja rodava com skip legitimo continua passando."""
    _com_skip(tmp_path)
    cfg = {"name": "T", "threshold": 50, "max_rounds": 3,
           "checks": [{"name": "suite", "cmd": "cd t && python3 -m pytest -q",
                       "weight": 100}]}
    r = _run(runner, tmp_path, cfg)
    assert "GATE=passed" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("runner", RUNNERS)
def test_min_passed_sem_sumario_e_not_verifiable(runner, tmp_path):
    """Saida sem numero de testes nao pode virar aprovado."""
    cfg = {"name": "T", "threshold": 50, "max_rounds": 3,
           "checks": [{"name": "mudo", "cmd": "true", "weight": 100,
                       "min_passed": 3}]}
    r = _run(runner, tmp_path, cfg)
    assert "NOT VERIFIABLE" in (r.stdout + r.stderr)
    assert "GATE=passed" not in r.stdout


@pytest.mark.parametrize("runner", RUNNERS)
def test_drift_sem_aprovacao_invalida_gate(runner, tmp_path):
    """O ataque: apagar o check de seguranca no round 2."""
    com_seg = {"name": "D", "threshold": 50, "max_rounds": 5, "checks": [
        {"name": "sec", "cmd": "false", "weight": 5, "severity": "block"},
        {"name": "lint", "cmd": "true", "weight": 95}]}
    _run(runner, tmp_path, com_seg, nome="d")
    sem_seg = {"name": "D", "threshold": 50, "max_rounds": 5,
               "checks": [{"name": "lint", "cmd": "true", "weight": 95}]}
    r = _run(runner, tmp_path, sem_seg, nome="d")
    saida = r.stdout + r.stderr
    # Mensagem mudou em 12/11/2026: agora carrega motivo= e o id derivado.
    assert "config_drift" in saida, saida
    assert "regua alterada sem aprovacao" in saida, saida
    assert r.returncode == 3


@pytest.mark.parametrize("runner", RUNNERS)
def test_approve_rebaseia_e_registra(runner, tmp_path):
    com_seg = {"name": "D", "threshold": 50, "max_rounds": 9, "checks": [
        {"name": "sec", "cmd": "false", "weight": 5, "severity": "block"},
        {"name": "lint", "cmd": "true", "weight": 95}]}
    _run(runner, tmp_path, com_seg, nome="a")
    sem_seg = {"name": "D", "threshold": 50, "max_rounds": 9,
               "checks": [{"name": "lint", "cmd": "true", "weight": 95}]}
    # Aprovacao exige o ID DERIVADO do drift + --approve-by (medido 12/11/2026:
    # qualquer id inventado re-baseava e liberava). Descobrir o id pelo bloqueio.
    bloqueio = _run(runner, tmp_path, sem_seg, nome="a")
    texto = bloqueio.stdout + bloqueio.stderr
    import re as _re
    m = _re.search(r"--approve ([0-9a-f]{8})", texto)
    assert m, f"bloqueio nao informou o id derivado: {texto}"
    apr_id = m.group(1)
    # id inventado continua recusado
    ruim = _run(runner, tmp_path, sem_seg, "--approve", "APR-1",
                "--approve-by", "t", nome="a")
    assert ruim.returncode == 3, "id inventado NAO pode aprovar"
    r = _run(runner, tmp_path, sem_seg, "--approve", apr_id,
             "--approve-by", "samuel", nome="a")
    assert "GATE=passed" in r.stdout, r.stdout + r.stderr
    st = json.loads((tmp_path / ".a-state.json").read_text(encoding="utf-8"))
    assert st["approval_id"] == apr_id
    assert st["approval_by"] == "samuel"
    assert st["approval_snapshot"]["drift"], "snapshot do que foi aprovado e obrigatorio"
    assert st["approval_ts"]
    # re-baseado: o round seguinte nao pode acusar drift de novo
    r2 = _run(runner, tmp_path, sem_seg, nome="a")
    assert "GATE=passed" in r2.stdout, r2.stdout + r2.stderr
