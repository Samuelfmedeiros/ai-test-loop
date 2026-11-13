"""Fingerprint derivado do schema: nenhuma chave do config pode ficar de fora.

Furo medido 12/11/2026: `strict_tests`, `min_passed` e `allow_skipped` chegaram
depois do fingerprint e nao entraram no hash — o agente desligava o anti-cheat
entre rounds e nenhum drift disparava. Lista literal sempre atrasa o proximo
adendo, entao o hash deriva de TOP_LEVEL_KEYS/CHECK_KEYS e ESTE teste falha
quando o loader conhece uma chave que o schema nao cobre.

Rodar: python3 -m pytest tests/test_fingerprint_schema.py -q
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RUNNER = RAIZ / "scripts" / "loop_runner.py"
LOADER = RAIZ / "scripts" / "loop_runner.py"


def _carrega_runner():
    spec = importlib.util.spec_from_file_location("lr_schema", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lr_schema"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_schema_cobre_todas_as_chaves_lidas_do_config():
    """Toda chave que o runner LE do config precisa estar no schema do hash."""
    mod = _carrega_runner()
    origem = LOADER.read_text(encoding="utf-8")

    # Chaves de check que o codigo le: c.get("x") / cfg["x"] / c["x"]
    lidas_check = set(re.findall(r'c(?:hk)?\.get\(\s*"([a-z_]+)"', origem))
    lidas_check |= set(re.findall(r'c(?:hk)?\[\s*"([a-z_]+)"\s*\]', origem))
    # Chaves top-level
    lidas_top = set(re.findall(r'cfg\.get\(\s*"([a-z_]+)"', origem))
    lidas_top |= set(re.findall(r'cfg\[\s*"([a-z_]+)"\s*\]', origem))

    # Ruido: chaves de state/report/args, nao de config
    # `checks` e tratado a parte no schema (lista de dicts, hash campo a campo).
    ruido = {
        "reasons", "passed", "duration_s", "weight", "evidence", "name",
        "cmd", "severity", "required", "checks",
    }
    fora_check = {
        k for k in lidas_check - ruido
        if k not in mod.CHECK_KEYS and k not in mod.FINGERPRINT_EXCLUDE
    }
    fora_top = {
        k for k in lidas_top - ruido
        if k not in mod.TOP_LEVEL_KEYS and k not in mod.FINGERPRINT_EXCLUDE
    }
    assert not fora_check, (
        f"chaves de check lidas do config mas AUSENTES do schema do fingerprint "
        f"(alterar nao dispara drift): {sorted(fora_check)}")
    assert not fora_top, (
        f"chaves top-level lidas do config mas AUSENTES do schema do fingerprint: "
        f"{sorted(fora_top)}")


def test_antichat_muda_fingerprint():
    """Exploit do §9.4: R1 com strict_tests, R2 sem -> TEM de dar drift."""
    mod = _carrega_runner()
    base = {"name": "x", "threshold": 50, "max_rounds": 3, "strict_tests": True,
            "checks": [{"name": "suite", "cmd": "pytest -q", "weight": 100}]}
    sha_com = mod.config_fingerprint(base)
    sem = json.loads(json.dumps(base))
    sem["strict_tests"] = False
    sha_sem = mod.config_fingerprint(sem)
    assert sha_com != sha_sem, "desligar strict_tests NAO mudou o hash — drift cego"

    # allow_skipped / min_passed tambem
    com_as = json.loads(json.dumps(base))
    com_as["checks"][0].update({"allow_skipped": False, "min_passed": 3})
    sem_as = json.loads(json.dumps(base))
    sem_as["checks"][0].pop("allow_skipped", None)
    assert mod.config_fingerprint(com_as) != mod.config_fingerprint(sem_as), \
        "remover allow_skipped/min_passed NAO mudou o hash — anti-cheat evadivel"


def test_blocking_gaps_entra_no_hash():
    """Desligar `blocking_gaps` entre rounds tambem precisa ser drift."""
    mod = _carrega_runner()
    a = {"name": "x", "threshold": 50, "max_rounds": 3, "blocking_gaps": ["skip"],
         "checks": [{"name": "s", "cmd": "pytest -q", "weight": 100}]}
    b = json.loads(json.dumps(a))
    b.pop("blocking_gaps")
    assert mod.config_fingerprint(a) != mod.config_fingerprint(b), \
        "remover blocking_gaps NAO mudou o hash"


def test_nome_do_projeto_nao_e_drift():
    """Rotulo nao e regua: renomear o projeto nao pode invalidar o gate."""
    mod = _carrega_runner()
    a = {"name": "proj-a", "threshold": 50, "max_rounds": 3,
         "checks": [{"name": "s", "cmd": "pytest -q", "weight": 100}]}
    b = json.loads(json.dumps(a))
    b["name"] = "proj-b"
    assert mod.config_fingerprint(a) == mod.config_fingerprint(b)


def test_runner_fingerprint_tem_hash_dos_dois_runners():
    mod = _carrega_runner()
    fp = mod.runner_fingerprint()
    assert len(fp["py_sha256"]) == 64
    assert fp.get("js_sha256") and len(fp["js_sha256"]) == 64
    assert fp["version"]


def test_drift_approval_id_e_derivado_e_estavel():
    mod = _carrega_runner()
    d = {"expected": "aaaa", "actual": "bbbb"}
    a = mod.drift_approval_id(d)
    b = mod.drift_approval_id({"actual": "bbbb", "expected": "aaaa"})
    assert a == b, "ordem das chaves nao pode mudar o id"
    assert len(a) == 8
    assert a != mod.drift_approval_id({"expected": "aaaa", "actual": "cccc"})
