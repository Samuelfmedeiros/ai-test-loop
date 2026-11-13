"""Cobertura de parse_test_counts / parse_junit_xml — os 87 mutantes sobreviventes.

O mutation testing apontou 87 dos 179 sobreviventes concentrados em
`parse_test_counts`: os testes existentes só exercitavam o caminho feliz do pytest.
Os ramos sem cobertura eram justamente os que decidem se um check CEGO passa verde
(jest, node:test TAP, fallback de progresso, junit vazio).

Regra que estes testes travam: ausência de número = `{}` (NOT VERIFICÁVEL), nunca
`0 passed` — que leria como "rodou e nada passou".
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import scripts.gate_core as gc  # noqa: E402


def _xml(tmp_path, corpo):
    p = tmp_path / "junit.xml"
    p.write_text(corpo, encoding="utf-8")
    return str(p)


# ── parse_junit_xml ──────────────────────────────────────────────────────
def test_xml_conta_por_caso(tmp_path):
    c = _xml(tmp_path, "<testsuite><testcase name='a'/><testcase name='b'>"
                       "<failure/></testcase><testcase name='c'><skipped/>"
                       "</testcase></testsuite>")
    d = gc.parse_junit_xml(c)
    assert (d["passed"], d["failed"], d["skipped"]) == (1, 1, 1)


def test_xml_error_conta_como_failed(tmp_path):
    c = _xml(tmp_path, "<testsuite><testcase><error/></testcase></testsuite>")
    assert gc.parse_junit_xml(c)["failed"] == 1


def test_xml_so_testcases_passados(tmp_path):
    c = _xml(tmp_path, "<testsuite><testcase/><testcase/></testsuite>")
    d = gc.parse_junit_xml(c)
    assert d["passed"] == 2
    assert d.get("failed", 0) == 0


def test_xml_sem_testcase_devolve_vazio(tmp_path):
    c = _xml(tmp_path, "<testsuite></testsuite>")
    assert gc.parse_junit_xml(c) == {}


def test_xml_invalido_devolve_vazio(tmp_path):
    c = _xml(tmp_path, "<nao-fechado")
    assert gc.parse_junit_xml(c) == {}


def test_xml_inexistente_devolve_vazio(tmp_path):
    assert gc.parse_junit_xml(str(tmp_path / "naoexiste.xml")) == {}


def test_xml_case_com_skip_e_failure_juntos(tmp_path):
    """Um caso que falhou e foi pulado não existe; falha tem precedência."""
    c = _xml(tmp_path, "<testsuite><testcase><failure/><skipped/></testcase>"
                       "</testsuite>")
    d = gc.parse_junit_xml(c)
    assert d["failed"] == 1
    assert d.get("skipped", 0) == 0


# ── parse_test_counts: pytest ────────────────────────────────────────────
@pytest.mark.parametrize("saida,esperado", [
    ("5 passed in 0.42s", {"passed": 5}),
    ("5 passed, 2 failed in 1.2s", {"passed": 5, "failed": 2}),
    ("3 passed, 1 skipped", {"passed": 3, "skipped": 1}),
    ("1 passed, 1 xfailed", {"passed": 1, "xfailed": 1}),
    ("1 passed, 1 xpassed", {"passed": 1, "xpassed": 1}),
    ("2 passed, 1 error", {"passed": 2, "error": 1}),
    ("2 passed, 1 errors", {"passed": 2, "error": 1}),  # normaliza o plural
    ("0 passed", {"passed": 0}),
])
def test_pytest_sumario(saida, esperado):
    assert gc.parse_test_counts(saida) == esperado


def test_pytest_soma_repeticoes():
    """Mesmo rótulo em duas linhas acumula (não sobrescreve)."""
    assert gc.parse_test_counts("2 passed\n3 passed")["passed"] == 5


# ── parse_test_counts: jest / vitest ─────────────────────────────────────
def test_jest_tests_colon():
    """Regressao 12/11/2026: 'Tests:' era contado 2x (generico + jest) e
    'Test Suites: 1 passed' somava suite como teste — 10 testes viravam 21."""
    saida = ("PASS  src/a.test.js\n"
             "Tests:       2 skipped, 10 passed, 12 total\n"
             "Test Suites: 1 passed, 1 total\n"
             "Time:        1.2 s")
    d = gc.parse_test_counts(saida)
    assert d["skipped"] == 2
    assert d["passed"] == 10, f"passed inflado quebra min_passed: {d}"


def test_jest_uma_categoria_so():
    assert gc.parse_test_counts("Tests: 7 passed")["passed"] == 7


def test_jest_linha_sem_numero_e_ignorada():
    assert gc.parse_test_counts("Tests: tudo ok") == {}


# ── parse_test_counts: node:test TAP ─────────────────────────────────────
@pytest.mark.parametrize("linha,chave,n", [
    ("# pass 10", "pass", 10),
    ("# fail 2", "fail", 2),
    ("# skip 3", "skip", 3),
    ("# todo 1", "todo", 1),
    ("# cancelled 4", "cancelled", 4),
])
def test_node_tap(linha, chave, n):
    assert gc.parse_test_counts(linha) == {chave: n}


def test_node_tap_precisa_estar_no_inicio_da_linha():
    """TAP só conta em linha própria — '# pass 10' no meio de texto não é resumo."""
    assert gc.parse_test_counts("log qualquer # pass 10") == {}


def test_node_tap_nao_casa_com_espaco_antes_do_hash():
    assert gc.parse_test_counts("  # pass 10") == {}


# ── parse_test_counts: fallback de progresso ─────────────────────────────
@pytest.mark.parametrize("linha,esperado", [
    (".s   [100%]", {"passed": 1, "skipped": 1}),
    ("..F  [100%]", {"passed": 2, "failed": 1}),
    ("xX   [100%]", {"xfailed": 1, "xpassed": 1}),
    ("E    [100%]", {"error": 1}),
    (".sxXFE [100%]", {"passed": 1, "skipped": 1, "xfailed": 1,
                       "xpassed": 1, "failed": 1, "error": 1}),
])
def test_progresso_pytest(linha, esperado):
    """Com warnings o pytest suprime o sumário e imprime só o progresso.

    Sem este ramo o check fica CEGO e passa verde com teste pulado.
    """
    assert gc.parse_test_counts(linha) == esperado


def test_progresso_sem_percentual_tambem_conta():
    assert gc.parse_test_counts("...")["passed"] == 3


def test_progresso_ignora_linha_que_nao_e_progresso():
    assert gc.parse_test_counts("texto qualquer .s no meio") == {}


def test_sumario_vence_progresso():
    """Sumário textual é mais confiável que contagem de pontos."""
    d = gc.parse_test_counts("5 passed\n.s\n")
    assert d.get("passed") == 5


# ── parse_test_counts: precedência e ausência ────────────────────────────
def test_junit_vence_texto(tmp_path):
    c = _xml(tmp_path, "<testsuite><testcase><failure/></testcase></testsuite>")
    d = gc.parse_test_counts("5 passed, 0 failed", junit_path=c)
    assert d["failed"] == 1


def test_junit_vazio_cai_no_texto(tmp_path):
    c = _xml(tmp_path, "<testsuite></testsuite>")
    assert gc.parse_test_counts("5 passed", junit_path=c)["passed"] == 5


def test_junit_inexistente_cai_no_texto(tmp_path):
    d = gc.parse_test_counts("5 passed", junit_path=str(tmp_path / "x.xml"))
    assert d["passed"] == 5


def test_sem_resumo_e_vazio_nao_zero():
    assert gc.parse_test_counts("erro sem contagem") == {}


def test_vazio_devolve_vazio():
    assert gc.parse_test_counts("") == {}
