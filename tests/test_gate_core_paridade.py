"""gate_core.py e loop_runner.py não podem divergir (fonte única do gate).

O core foi extraído para ser alvo viável de mutation testing: o runner tem 864
linhas e o mutmut expande o módulo para 22 MB — compilar o mutado levava >180s e a
rodada nunca terminava (3 tentativas mortas por timeout). Este módulo tem 262
linhas, compila em milissegundos.

O risco da extração é virar segunda verdade: alguém ajusta `score()` no runner, a
mutação testa uma cópia morta, e o gate real fica sem cobertura. O teste abaixo é
o que impede isso.
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import scripts.gate_core as gate_core  # noqa: E402
import scripts.loop_runner as loop_runner  # noqa: E402

FUNCOES = [
    "score", "config_fingerprint", "drift_approval_id", "migrate_config",
    "validate_config", "parse_junit_xml", "parse_test_counts",
    "runner_fingerprint", "_sha256_file",
]
CONSTANTES = ["TOP_LEVEL_KEYS", "CHECK_KEYS", "FINGERPRINT_EXCLUDE", "RUNNER_VERSION"]


def test_runner_reexporta_o_core():
    """A API pública do runner tem de continuar existindo."""
    faltando = [f for f in FUNCOES + CONSTANTES + ["ConfigInvalida"]
                if not hasattr(loop_runner, f)]
    assert not faltando, f"loop_runner.py nao expoe {faltando} apos a extracao"


def test_runner_nao_redefine():
    """Redefinir = duas verdades. O runner importa, não recopia."""
    src = (REPO / "scripts" / "loop_runner.py").read_text(encoding="utf-8")
    redef = [f for f in FUNCOES if f"\ndef {f}(" in src]
    redef += [c for c in CONSTANTES if f"\n{c} = " in src]
    redef += ["ConfigInvalida"] if "\nclass ConfigInvalida" in src else []
    assert not redef, (
        f"loop_runner.py redefine {redef} — a mutacao testaria a copia morta e o "
        f"gate real ficaria sem cobertura")


@pytest.mark.parametrize("nome", CONSTANTES)
def test_constantes_identicas(nome):
    """Objeto é o MESMO (import), então divergir é impossível — provado por identidade."""
    assert getattr(loop_runner, nome) is getattr(gate_core, nome)


def test_comportamento_identico_score():
    checks = [{"passed": True, "weight": 60}, {"passed": False, "weight": 40}]
    assert loop_runner.score(checks) == gate_core.score(checks) == 60.0


def test_comportamento_identico_fingerprint():
    cfg = {"name": "x", "threshold": 80,
           "checks": [{"name": "a", "cmd": "true", "weight": 10, "severity": "block"}]}
    assert loop_runner.config_fingerprint(cfg) == gate_core.config_fingerprint(cfg)


def test_core_nao_importa_o_runner():
    """Dependência circular quebraria a mutação isolada do core."""
    src = (REPO / "scripts" / "gate_core.py").read_text(encoding="utf-8")
    assert "import loop_runner" not in src
    assert "from loop_runner" not in src
