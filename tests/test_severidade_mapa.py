"""Mapa de severidade por classe de falha (12/11/2026).

Contexto: 62 critics, dos quais 59 descontavam ponto em vez de bloquear. Gap de
segurança valia -5 num rubric de 100 — o mesmo peso de um lint. Este teste trava
o mapa para que a classificação não vire prosa: cada classe bloqueante PRECISA
casar gaps que os critics realmente emitem, e as classes de aviso NÃO podem
bloquear.

Fonte do mapa: `data/mapa-severidade-62-critics.json` (versionado junto com o repo,
para o teste rodar em qualquer maquina — inclusive no CI, que nao tem ~/.hermes).
O override por env `ROGER_SEV_MAP` continua valendo para apontar ao mapa vivo do
orquestrador durante a varredura de uma maquina especifica.
"""
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Fallback em cadeia: o mapa VERSIONADO no repo (portavel, roda no CI) primeiro;
# o caminho do perfil so como ultimo recurso de quem roda na maquina do dono.
_CANDIDATOS = [
    os.path.join(RAIZ, "data", "mapa-severidade-62-critics.json"),
    os.path.expanduser("~/.hermes/profiles/ajudante/state/mapa-severidade-62-critics.json"),
]
MAPA_PADRAO = next((c for c in _CANDIDATOS if os.path.isfile(c)), _CANDIDATOS[0])


def _carrega():
    caminho = os.environ.get("ROGER_SEV_MAP", MAPA_PADRAO)
    if not os.path.isfile(caminho):
        return None
    with open(caminho, encoding="utf-8") as fh:
        return json.load(fh)


def test_mapa_existe():
    m = _carrega()
    assert m is not None, (
        f"mapa de severidade ausente em {MAPA_PADRAO} — sem ele os 59 critics "
        f"voltam a descontar ponto em vez de bloquear")
    assert m["classes"], "mapa sem classes"
    assert "_precedencia" in m["classes"], "mapa sem ordem de precedencia"


def test_toda_classe_bloqueante_casa_gap_real():
    """Classe que bloqueia sem casar gap real = regex morto, trava decorativa."""
    m = _carrega()
    if m is None:
        return
    classes = m["classes"]
    reais = m["gaps_reais"]
    mortas = []
    for nome, d in classes.items():
        if nome.startswith("_") or not d.get("bloqueia"):
            continue
        casados = [g for g in reais if any(re.search(rx, g, re.I) for rx in d["padroes"])]
        if not casados:
            mortas.append(nome)
    assert not mortas, f"classes bloqueantes sem gap real (regex morto): {mortas}"


def test_regex_do_mapa_sao_validos():
    """Regex invalido bloqueia tudo (fail-closed) — nao pode entrar no mapa."""
    m = _carrega()
    if m is None:
        return
    for nome, d in m["classes"].items():
        if nome.startswith("_"):
            continue
        for padrao in d["padroes"]:
            re.compile(padrao)  # levanta se invalido


def test_classes_de_aviso_nao_bloqueiam():
    """Estilo/infra/ambiente são ausência de prova ou reversíveis — não bloqueiam."""
    m = _carrega()
    if m is None:
        return
    classes = m["classes"]
    for nome in ("estilo", "infra", "ambiente"):
        assert nome in classes, f"classe {nome} sumiu do mapa"
        assert classes[nome]["bloqueia"] is False, (
            f"{nome} nao pode bloquear entrega — {classes[nome]['porque']}")


def test_precedencia_resolve_conflito():
    """`X ausente — sem medição no navegador` casa testes E ambiente: a ordem decide."""
    m = _carrega()
    if m is None:
        return
    ordem = m["classes"]["_precedencia"]
    assert ordem.index("testes") < ordem.index("ambiente"), (
        "testes tem de vir antes de ambiente: gap de teste nao pode virar "
        "'nao verificavel' e escapar do bloqueio")
    assert ordem.index("seguranca") < ordem.index("estilo")
    assert ordem.index("antifraude") < ordem.index("testes")


def test_gaps_do_mapa_batem_com_critics():
    """Amarrar o mapa a string que nao existe em critic nenhum = teatro."""
    m = _carrega()
    if m is None:
        return
    if not m.get("critics_usados"):
        return
    scripts = os.path.expanduser("~/.hermes/scripts")
    if not os.path.isdir(scripts):
        return
    reais = set()
    for arq in os.listdir(scripts):
        if not arq.startswith("critic_check_") or not arq.endswith(".py"):
            continue
        with open(os.path.join(scripts, arq), encoding="utf-8", errors="replace") as fh:
            texto = fh.read()
        reais.update(mm.group(1).strip() for mm in
                     re.finditer(r'gaps?\.append\(\s*f?["\']([^"\']{3,90})', texto))
    if not reais:
        return  # ambiente sem os critics (CI do repo público)
    cobertos = set(m.get("mapa", {}))
    assert cobertos, "mapa vazio"
    # os gaps reais que o mapa cobre têm de existir nos critics
    inventados = sorted(p for p in cobertos
                        if not any(p.startswith(r[:20]) for r in reais))
    assert len(inventados) <= len(cobertos) * 0.5, (
        f"mais da metade dos gaps do mapa nao existe em critic nenhum: "
        f"{inventados[:5]}")
