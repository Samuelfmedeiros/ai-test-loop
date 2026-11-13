"""Testes UNITÁRIOS do gate — importam o módulo, não chamam subprocess.

Por que este arquivo existe (medido 12/11/2026): 9 dos 10 arquivos de teste
executam `loop_runner.py` como subprocess, e dentro de `mutants/` o runner não
encontra a config do mutmut — o mutante morre com erro de ambiente, não por
assert. Mutation testing com subprocess é CEGO: todo mutante parece "morto" por
timeout/crash, inflando o score sem provar nada.

Só teste que importa a função mata mutante de verdade. Aqui ficam os casos das
funções que decidem o gate: score, config_fingerprint, drift_approval_id,
migrate_config, validate_config, parse_junit_xml, parse_test_counts.
"""
import json
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import scripts.loop_runner as lr  # noqa: E402


# ── score ────────────────────────────────────────────────────────────────
def test_score_tudo_passou_e_100():
    assert lr.score([{"passed": True, "weight": 10}]) == 100.0


def test_score_nada_passou_e_0():
    assert lr.score([{"passed": False, "weight": 10}]) == 0.0


def test_score_pondera_por_peso_nao_por_contagem():
    checks = [{"passed": True, "weight": 90}, {"passed": False, "weight": 10}]
    assert lr.score(checks) == 90.0


def test_score_sem_checks_nao_divide_por_zero():
    assert lr.score([]) == 0.0


# ── config_fingerprint ───────────────────────────────────────────────────
def _cfg():
    return {"name": "p", "threshold": 80, "max_rounds": 3,
            "checks": [{"name": "a", "cmd": "pytest -q", "weight": 50,
                        "severity": "block", "required": True}]}


def test_fingerprint_e_estavel_para_config_identica():
    assert lr.config_fingerprint(_cfg()) == lr.config_fingerprint(_cfg())


def test_fingerprint_muda_quando_severity_muda():
    c1 = _cfg()
    c2 = _cfg()
    c2["checks"][0]["severity"] = "score"
    assert lr.config_fingerprint(c1) != lr.config_fingerprint(c2)


def test_fingerprint_muda_quando_required_muda():
    c1 = _cfg(); c2 = _cfg(); c2["checks"][0]["required"] = False
    assert lr.config_fingerprint(c1) != lr.config_fingerprint(c2)


def test_fingerprint_muda_com_max_rounds():
    c1 = _cfg(); c2 = _cfg(); c2["max_rounds"] = 9
    assert lr.config_fingerprint(c1) != lr.config_fingerprint(c2)


@pytest.mark.parametrize("campo", ["cmd", "weight", "must_match",
                                   "fail_if_output_matches", "expect_exit",
                                   "timeout_s", "cwd", "junit_xml"])
def test_fingerprint_cobre_cada_chave_do_check(campo):
    """Chave lida pelo runner e fora do hash = régua mutável sem drift."""
    c1 = _cfg()
    c2 = _cfg()
    c2["checks"][0][campo] = "zzz" if campo in ("cmd", "must_match",
                                               "fail_if_output_matches", "cwd",
                                               "junit_xml") else 99
    assert lr.config_fingerprint(c1) != lr.config_fingerprint(c2), (
        f"{campo} nao entra no fingerprint — mudar {campo} entre rounds nao "
        f"dispara drift")


# ── drift_approval_id ────────────────────────────────────────────────────
def test_approval_id_e_derivado_do_drift():
    a = lr.drift_approval_id({"expected": "aaaa", "actual": "bbbb"})
    b = lr.drift_approval_id({"expected": "aaaa", "actual": "bbbb"})
    assert a == b
    assert re.fullmatch(r"[0-9a-f]{8}", a), a


def test_approval_id_muda_com_o_drift():
    a = lr.drift_approval_id({"expected": "aaaa", "actual": "bbbb"})
    b = lr.drift_approval_id({"expected": "aaaa", "actual": "cccc"})
    assert a != b, "id fixo permitiria aprovar drift qualquer"


# ── validate_config ──────────────────────────────────────────────────────
def test_required_sem_severity_e_erro():
    cfg = {"checks": [{"name": "x", "cmd": "true", "weight": 1, "required": True}]}
    with pytest.raises(lr.ConfigInvalida):
        lr.validate_config(cfg)


def test_required_com_severity_passa():
    cfg = {"checks": [{"name": "x", "cmd": "true", "weight": 1,
                       "required": True, "severity": "block"}]}
    lr.validate_config(cfg)  # não levanta


def test_sem_required_passa():
    cfg = {"checks": [{"name": "x", "cmd": "true", "weight": 1}]}
    lr.validate_config(cfg)


# ── migrate_config ───────────────────────────────────────────────────────
def test_migrate_required_true_vira_block():
    cfg = {"checks": [{"name": "sec", "cmd": "true", "weight": 1, "required": True}]}
    novo, mud = lr.migrate_config(cfg)
    assert novo["checks"][0]["severity"] == "block"
    assert any("severity" in m for m in mud)


def test_migrate_nao_inventa_severity_em_check_opcional():
    cfg = {"checks": [{"name": "lint", "cmd": "true", "weight": 1}]}
    _, mud = lr.migrate_config(cfg)
    assert "severity" not in cfg["checks"][0]
    assert mud == []


def test_migrate_strict_tests_completa_allow_skipped():
    cfg = {"strict_tests": True,
           "checks": [{"name": "t", "cmd": "pytest", "weight": 1,
                       "severity": "block"}]}
    lr.migrate_config(cfg)
    assert cfg["checks"][0]["allow_skipped"] is False


def test_migrate_preserva_severity_explicito():
    cfg = {"checks": [{"name": "s", "cmd": "true", "weight": 1,
                       "required": True, "severity": "score"}]}
    _, mud = lr.migrate_config(cfg)
    assert cfg["checks"][0]["severity"] == "score"
    assert mud == []


def test_migrate_e_idempotente():
    cfg = {"checks": [{"name": "s", "cmd": "true", "weight": 1, "required": True}]}
    lr.migrate_config(cfg)
    _, mud = lr.migrate_config(cfg)
    assert mud == []


# ── parse_test_counts ────────────────────────────────────────────────────
def test_conta_passed_e_failed():
    r = lr.parse_test_counts("5 passed, 2 failed in 1.2s")
    assert (r["passed"], r["failed"]) == (5, 2)


def test_conta_skipped():
    r = lr.parse_test_counts("3 passed, 1 skipped")
    assert r["passed"] == 3
    assert r["skipped"] == 1


def test_saida_sem_resumo_nao_inventa_numero():
    """{} e NOT VERIFIABLE — nunca 0 passed, que leria como 'rodou e nada passou'."""
    r = lr.parse_test_counts("erro qualquer sem contagem")
    assert r == {}, f"sem resumo tem de ser {{}} (ausencia), veio {r}"


def test_junit_tem_precedencia_sobre_o_texto():
    """Saída mentirosa ("5 passed") perde para o XML real."""
    import tempfile
    xml = ("<?xml version=\"1.0\"?><testsuite>"
           "<testcase name=\"a\"/><testcase name=\"b\"><failure/></testcase>"
           "<testcase name=\"c\"><skipped/></testcase></testsuite>")
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as fh:
        fh.write(xml); caminho = fh.name
    try:
        r = lr.parse_test_counts("5 passed, 0 failed", junit_path=caminho)
        assert r["failed"] == 1, "JUnit tem de vencer o texto"
        assert r["skipped"] == 1
        assert r["passed"] == 1
    finally:
        pathlib.Path(caminho).unlink(missing_ok=True)


def test_junit_ausente_cai_no_texto():
    r = lr.parse_test_counts("7 passed", junit_path="/nao/existe.xml")
    assert r["passed"] == 7


# ── parse_junit_xml ──────────────────────────────────────────────────────
def test_junit_parseia_por_caso_e_nao_por_atributo():
    """Conta `<testcase>` (fonte que o framework escreve), não atributo do suite."""
    import tempfile
    xml = ("<?xml version=\"1.0\"?><testsuite>"
           "<testcase name=\"a\"/><testcase name=\"b\">"
           "<failure/></testcase><testcase name=\"c\"><skipped/></testcase>"
           "</testsuite>")
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as fh:
        fh.write(xml); caminho = fh.name
    try:
        d = lr.parse_junit_xml(caminho)
        assert d["passed"] == 1
        assert d["failed"] == 1
        assert d["skipped"] == 1
    finally:
        pathlib.Path(caminho).unlink(missing_ok=True)


def test_junit_invalido_devolve_dict_vazio():
    """{} = NOT VERIFIABLE (quem exige numero trata como ausencia, nao aprovacao)."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as fh:
        fh.write("<nao-e-xml"); caminho = fh.name
    try:
        assert lr.parse_junit_xml(caminho) == {}
    finally:
        pathlib.Path(caminho).unlink(missing_ok=True)


def test_junit_sem_testcase_devolve_vazio():
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as fh:
        fh.write("<testsuite></testsuite>"); caminho = fh.name
    try:
        assert lr.parse_junit_xml(caminho) == {}
    finally:
        pathlib.Path(caminho).unlink(missing_ok=True)
