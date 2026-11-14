#!/usr/bin/env python3
"""`--config` que nao existe nao pode matar a execucao em 3ms.

Por que existe (13/11/2026): a telemetria da loja mostrou **51 erros contra 23
runs** no CLI. A maioria morria em ~3ms, antes de qualquer teste rodar, porque a
pessoa seguia a documentacao (`--config loop.json`) de dentro do proprio projeto
e o arquivo nao existia. Nao era falta de interesse: era um erro de 3ms no lugar
do primeiro resultado.

O comportamento correto: avisar e montar o teste do diretorio atual. Quem quer
comandos proprios cria o loop.json depois — o schema e detalhe, nao pre-requisito.

Roda com `python3 tests/test_config_ausente.py`, sem pytest (stdlib-only).
"""
import os
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_RUNNER = os.path.join(RAIZ, "scripts", "loop_runner.py")


class TestConfigAusente(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="atl-cfg-")
        # projeto python minimo: sinal de linguagem + teste real
        with open(os.path.join(self.tmp, "requirements.txt"), "w") as fh:
            fh.write("pytest\n")
        with open(os.path.join(self.tmp, "calc.py"), "w") as fh:
            fh.write("def soma(a, b):\n    return a + b\n")
        with open(os.path.join(self.tmp, "test_calc.py"), "w") as fh:
            fh.write("from calc import soma\n\n\ndef test_soma():\n    assert soma(2, 2) == 4\n")

    def test_config_inexistente_cai_no_auto_probe(self):
        """O caso medido: `--config loop.json` num projeto que nao tem o arquivo."""
        env = dict(os.environ)
        env["AI_TEST_LOOP_NO_TELEMETRY"] = "1"
        p = subprocess.run(
            [sys.executable, PY_RUNNER, "--config", "loop.json"],
            cwd=self.tmp, capture_output=True, text=True, timeout=300, env=env,
        )
        saida = p.stdout + p.stderr
        # nao pode ser o erro fatal de antes
        self.assertNotIn("config not found", saida)
        # tem de avisar que montou sozinho
        self.assertIn("nao existe", saida)
        # e tem de ter chegado a medir algo (nota ou relatorio), nao morrer calado
        self.assertTrue(
            "SCORE=" in saida or "Nota:" in saida or "Nenhum teste" in saida,
            f"nao chegou a medir nada:\n{saida[:800]}",
        )

    def test_config_valido_continua_mandando(self):
        """Config que existe nao pode ser ignorado em favor do auto-probe."""
        cfg = os.path.join(self.tmp, "meu.json")
        with open(cfg, "w") as fh:
            fh.write('{"checks": [{"name": "meu check", "cmd": "echo ok", "weight": 1}]}')
        env = dict(os.environ)
        env["AI_TEST_LOOP_NO_TELEMETRY"] = "1"
        p = subprocess.run(
            [sys.executable, PY_RUNNER, "--config", cfg, "--dry-run"],
            cwd=self.tmp, capture_output=True, text=True, timeout=120, env=env,
        )
        self.assertIn("meu check", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
