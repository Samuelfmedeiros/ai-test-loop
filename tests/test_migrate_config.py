"""Migração de config 1.1.x -> 1.2.0 (breaking change do §9.1).

Medido em 12/11/2026: `required: true` sem `severity` passou a ser erro de carga
(era ambiguidade de gate explorável), e nenhuma das 53 configs locais quebrou —
mas todo usuário de pip/npx que atualizar recebe "invalid config" sem saber o que
fazer. Migração silenciosa obrigatória no mesmo release do breaking change.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "scripts" / "loop_runner.py"
RUNNER_JS = REPO / "scripts" / "loop_runner.js"


def _roda(*args):
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        capture_output=True, text=True, timeout=120,
    )


def _config_antiga(tmp_path):
    cfg = {
        "name": "migr",
        "threshold": 50,
        "max_rounds": 2,
        "strict_tests": True,
        "checks": [
            {"name": "sec", "cmd": "true", "weight": 50, "required": True},
            {"name": "lint", "cmd": "true", "weight": 50},
        ],
    }
    p = tmp_path / "loop.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_config_antiga_falha_com_instrucao_de_cura(tmp_path):
    """O erro de carga não pode ser um beco: tem que nomear o comando que cura."""
    cfg = _config_antiga(tmp_path)
    r = _roda("--config", str(cfg))
    assert r.returncode != 0
    assert "required=true sem severity" in r.stdout + r.stderr
    assert "--migrate-config" in r.stdout + r.stderr


def test_dry_run_nao_grava(tmp_path):
    cfg = _config_antiga(tmp_path)
    antes = cfg.read_text(encoding="utf-8")
    r = _roda("--config", str(cfg), "--migrate-config", "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DRY-RUN" in r.stdout
    assert cfg.read_text(encoding="utf-8") == antes, "dry-run escreveu no arquivo"


def test_migra_grava_backup_e_config_roda(tmp_path):
    cfg = _config_antiga(tmp_path)
    r = _roda("--config", str(cfg), "--migrate-config")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "loop.json.bak-pre-migrate").exists(), "sem backup nao e migracao"

    dados = json.loads(cfg.read_text(encoding="utf-8"))
    assert dados["checks"][0]["severity"] == "block"
    # required=false ou ausente permanece sem severity (nao inventa bloqueio)
    assert "severity" not in dados["checks"][1]
    # strict_tests implicava allow_skipped=false
    assert dados["checks"][0].get("allow_skipped") is False

    r2 = _roda("--config", str(cfg), "--quiet")
    assert "GATE=" in r2.stdout, r2.stdout + r2.stderr


def test_migracao_idempotente(tmp_path):
    cfg = _config_antiga(tmp_path)
    _roda("--config", str(cfg), "--migrate-config")
    r = _roda("--config", str(cfg), "--migrate-config")
    assert r.returncode == 0
    assert "nada a migrar" in r.stdout
    # o json migrado nao pode mudar de novo
    assert json.loads(cfg.read_text(encoding="utf-8"))["checks"][0]["severity"] == "block"


def test_paridade_js_migra_igual(tmp_path):
    """O pacote publica os dois runners; divergencia entre eles e bug de distribuicao."""
    if not RUNNER_JS.exists():
        return
    cfg = _config_antiga(tmp_path)
    r = subprocess.run(
        ["node", str(RUNNER_JS), "--config", str(cfg), "--migrate-config"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    dados = json.loads(cfg.read_text(encoding="utf-8"))
    assert dados["checks"][0]["severity"] == "block"
    assert dados["checks"][0].get("allow_skipped") is False
