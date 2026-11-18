#!/usr/bin/env python3
"""Testes do modo rápido (`--repo`) — a porta de entrada de quem só quer ver a nota.

Por que existe: em 11/11/2026 o runner exigia `loop.json` escrito à mão. Quem chegava
para "ver se meu projeto está bom" tinha que aprender o schema antes de ver qualquer
resultado. O modo rápido elimina esse passo — e é justamente por isso que ele precisa
de teste: o risco não é quebrar, é **mentir** (dar nota alta sem ter rodado nada, ou
reprovar o projeto por falta de ferramenta na máquina).

Roda com `python3 tests/test_repo.py`, sem pytest: o pacote é stdlib-only por design.
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# o mesmo módulo vive em dois lugares: `scripts/` (repo) e `ai_test_loop/scripts/` (wheel).
# O teste aceita os dois para rodar tanto aqui quanto do pacote instalado.
for _p in (os.path.join(RAIZ, "scripts"), os.path.join(RAIZ, "ai_test_loop", "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import repo_probe  # noqa: E402


def _proj(files: dict) -> str:
    """Cria um projeto temporário com os arquivos dados."""
    d = tempfile.mkdtemp(prefix="atl-proj-")
    for nome, corpo in files.items():
        full = os.path.join(d, nome)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(corpo))
    return d


class TestDeteccao(unittest.TestCase):
    def test_projeto_python_com_testes_ganha_check_de_teste(self):
        d = _proj({"pyproject.toml": "name = 'meu-proj'\nversion = '1.0'\n",
                   "tests/test_a.py": "def test_ok():\n    assert True\n"})
        cfg, falt, _ = repo_probe.build_config(d)
        nomes = [c["name"] for c in cfg["checks"]]
        self.assertTrue(any("testes" in n for n in nomes), nomes)

    def test_nome_vem_do_pyproject_nao_da_pasta(self):
        d = _proj({"pyproject.toml": "name = 'nome-declarado'\n"})
        cfg, _, _ = repo_probe.build_config(d)
        self.assertEqual(cfg["name"], "nome-declarado")

    def test_nome_de_url_nao_vira_repo(self):
        # a pasta de clone sempre se chama 'repo'; o nome tem que vir da URL
        d = _proj({"README.md": "# x\n"})
        cfg, _, _ = repo_probe.build_config(d, "https://github.com/psf/requests")
        self.assertEqual(cfg["name"], "requests")

    def test_projeto_vazio_nao_inventa_check(self):
        d = _proj({"leiame.txt": "nada aqui\n"})
        cfg, _, _ = repo_probe.build_config(d)
        self.assertEqual(cfg["checks"], [])

    def test_projeto_node_com_node_test_ganha_check(self):
        """`node --test` + *.test.js é o padrão do Node moderno, sem dependência.

        Antes de 17/11/2026 este projeto recebia "Nenhum teste automático
        encontrado" e saía com 2 — falso negativo em quem já segue o padrão.
        """
        d = _proj({"package.json": '{"name":"demo","version":"1.0.0"}',
                   "index.js": "module.exports = 1;\n",
                   "soma.test.js": "const t=require('node:test');\n"})
        cfg, _, _ = repo_probe.build_config(d)
        nomes = [c["name"] for c in cfg["checks"]]
        self.assertTrue(any("node --test" in n for n in nomes), nomes)

    def test_node_com_scripts_test_prefere_npm(self):
        # scripts.test continua sendo a fonte da verdade quando existe
        d = _proj({"package.json": '{"name":"demo","scripts":{"test":"jest"}}',
                   "a.test.js": "// teste\n"})
        cfg, _, _ = repo_probe.build_config(d)
        nomes = [c["name"] for c in cfg["checks"]]
        self.assertTrue(any("(npm)" in n for n in nomes), nomes)
        self.assertFalse(any("node --test" in n for n in nomes), nomes)


class TestHonestidade(unittest.TestCase):
    """O risco do modo rápido não é quebrar: é dar veredito falso."""

    def test_ferramenta_ausente_nao_vira_check(self):
        d = _proj({"pyproject.toml": "name = 'solo'\n"})
        cfg, falt, _ = repo_probe.build_config(d)
        # sem testes no projeto, não pode existir check de testes
        self.assertFalse([c for c in cfg["checks"] if "testes" in c["name"]])

    def test_falta_de_dependencia_nao_e_falha_do_projeto(self):
        r = {"name": "testes", "passed": False, "exit_code": 1,
             "evidence": "ModuleNotFoundError: No module named 'requests'"}
        mantidos, falt = repo_probe.reclassify([r])
        self.assertEqual(mantidos, [])
        self.assertIn("requests", falt[0]["motivo"])

    def test_falha_real_de_teste_continua_sendo_falha(self):
        # rodou e reprovou: isto É veredito sobre o projeto, não sobre a máquina
        r = {"name": "testes", "passed": False, "exit_code": 1,
             "evidence": "7 failed, 407 passed, 15 skipped"}
        mantidos, falt = repo_probe.reclassify([r])
        self.assertEqual(len(mantidos), 1)
        self.assertEqual(falt, [])

    def test_comando_inexistente_nao_reprova_o_projeto(self):
        r = {"name": "testes (npm)", "passed": False, "exit_code": 127,
             "evidence": "/bin/sh: line 1: npm: command not found"}
        mantidos, falt = repo_probe.reclassify([r])
        self.assertEqual(mantidos, [])
        self.assertIn("não está instalado", falt[0]["motivo"])

    def test_lint_sozinho_nao_aprova(self):
        # passar só o lint (peso menor, required=False) não é veredito do projeto
        r = {"name": "padrão do código", "passed": True, "required": False,
             "exit_code": 0, "evidence": "All checks passed!"}
        self.assertTrue(repo_probe.inconclusive([r]))

    def test_node_test_com_falha_nao_vira_inconclusivo(self):
        """`node --test` não imprime "N passed": imprime `ℹ pass 0` / `ℹ fail 1`.

        Sem reconhecer esse formato, um teste que falhou era lido como "nada
        rodou" e o projeto passava sem veredito — falha real escondida.
        """
        r = {"name": "testes automáticos (node --test)", "passed": False,
             "exit_code": 1,
             "evidence": "ℹ tests 1\nℹ pass 0\nℹ fail 1\n✖ soma errada"}
        self.assertFalse(repo_probe.inconclusive([r]))

    def test_node_test_aprovado_nao_vira_inconclusivo(self):
        r = {"name": "testes automáticos (node --test)", "passed": True,
             "exit_code": 0, "evidence": "ℹ tests 1\nℹ pass 1\nℹ fail 0"}
        self.assertFalse(repo_probe.inconclusive([r]))


class TestSeguranca(unittest.TestCase):
    def test_acha_senha_escrita_no_codigo(self):
        # isca montada em runtime: escrita literal, o detector acusaria o próprio
        # arquivo de teste (aconteceu, medido 11/11/2026 ao rodar --repo no repo).
        isca = "sk-" + "abcdefghijklmnopqrstuvwx"
        d = _proj({"app.py": f"KEY = '{isca}'\n"})
        _, _, ach = repo_probe.build_config(d)
        self.assertTrue(ach["segredos"])
        self.assertEqual(ach["segredos"][0]["arquivo"], "app.py")

    def test_acha_caminho_pessoal(self):
        d = _proj({"conf.js": "const p = '/home/fulano/projetos/x';\n"})
        _, _, ach = repo_probe.build_config(d)
        self.assertTrue(ach["caminhos"])

    def test_ignora_node_modules(self):
        # segredo dentro de biblioteca de terceiro não é problema do usuário
        isca = "sk-" + "abcdefghijklmnopqrstuvwx"
        d = _proj({"node_modules/pkg/key.js": f"const k = '{isca}';\n"})
        _, _, ach = repo_probe.build_config(d)
        self.assertFalse(ach["segredos"])

    def test_nao_escreve_dentro_do_projeto(self):
        d = _proj({"pyproject.toml": "name = 'limpo'\n"})
        antes = set(os.listdir(d))
        repo_probe.build_config(d)
        self.assertEqual(set(os.listdir(d)), antes)


class TestCacheForaDoProjeto(unittest.TestCase):
    def test_cache_fica_em_home_nao_no_projeto(self):
        d = _proj({"pyproject.toml": "name = 'x'\n"})
        c = repo_probe.cache_dir(d)
        self.assertFalse(c.startswith(os.path.abspath(d)))
        self.assertIn(".cache", c)


class TestSaidaLegivel(unittest.TestCase):
    def test_resumo_nao_diz_que_passou_quando_faltou_rodar(self):
        cfg = {"name": "x", "threshold": 85, "checks": []}
        txt = repo_probe.human_report("/tmp", cfg, [{"nome": "testes", "motivo": "sem pytest"}],
                                      {"segredos": [], "caminhos": [], "env_versionado": []},
                                      100.0, True, [{"name": "lint", "passed": True,
                                                     "required": False, "evidence": "ok"}])
        self.assertIn("Não deu para julgar", txt)
        self.assertIn("NÃO é uma nota baixa", txt)


class TestConviteERelatorioSemVarredura(unittest.TestCase):
    """O convite do rodapé e o relatório quando não houve varredura de segurança.

    Dois defeitos medidos em 14/11/2026 no mesmo trecho:

    1. `human_report` estourava `KeyError: 'segredos'` quando o runner vem de
       `--config` (nesse caminho `achados` é `{}` e não tem as chaves da
       varredura). Rodava tudo, calculava nota e morria antes de imprimir.
    2. O pacote tinha 1284 downloads/semana e 28 cliques na loja em 30 dias:
       quem instala pelo terminal nunca vê o README. O convite só existe se for
       silencioso em CI — propaganda em log de pipeline quebra contrato.
    """

    def test_relatorio_nao_quebra_sem_varredura_de_seguranca(self):
        cfg = {"name": "x", "threshold": 60, "checks": []}
        txt = repo_probe.human_report("/tmp", cfg, [], {}, 100.0, True,
                                      [{"name": "soma", "passed": True,
                                        "required": False, "evidence": "ok"}])
        self.assertIn("Nota", txt)

    def test_convite_aparece_no_uso_normal(self):
        salvo = {k: os.environ.pop(k, None) for k in
                 ("ATL_NO_CTA", "NO_COLOR", "CI", "CONTINUOUS_INTEGRATION",
                  "BUILD_NUMBER", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL")}
        try:
            self.assertIn("skills.seu.pet", repo_probe.convite({}))
        finally:
            for k, v in salvo.items():
                if v is not None:
                    os.environ[k] = v

    def test_convite_cala_em_ci(self):
        salvo = os.environ.get("CI")
        os.environ["CI"] = "true"
        try:
            self.assertEqual(repo_probe.convite({}), "")
        finally:
            if salvo is None:
                os.environ.pop("CI", None)
            else:
                os.environ["CI"] = salvo

    def test_convite_cala_com_no_color(self):
        salvo = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            self.assertEqual(repo_probe.convite({}), "")
        finally:
            if salvo is None:
                os.environ.pop("NO_COLOR", None)
            else:
                os.environ["NO_COLOR"] = salvo


if __name__ == "__main__":
    unittest.main(verbosity=2)
