#!/usr/bin/env python3
"""Testes do CLI do ai-test-loop — o que o usuario ve ao rodar `npx ai-test-loop`.

Por que existe: a 1.0.2 ficou semanas publicada com `--version` quebrado e sem
argumento cuspindo erro cru do argparse, porque o pacote nao tinha NENHUM teste.
555 downloads no mes e nenhum sinal de que o comando documentado funcionava.

Nao usa pytest: o pacote e stdlib-only por design, e estes testes rodam com
`python3 tests/test_cli.py` em qualquer maquina com node.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(RAIZ, "bin", "cli.js")


def roda(*args, env_extra=None, espera_erro=False):
    """Executa o CLI e devolve (rc, stdout, stderr)."""
    env = dict(os.environ)
    # telemetria desligada: teste nao deve escrever no servidor do site
    env["AI_TEST_LOOP_NO_TELEMETRY"] = "1"
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        ["node", CLI, *args],
        capture_output=True, text=True, timeout=120, env=env,
    )
    return p.returncode, p.stdout, p.stderr


class TestAjuda(unittest.TestCase):
    def test_help_mostra_a_ajuda_do_produto(self):
        """`--help` tem de explicar o que a ferramenta faz, nao o usage do Python."""
        rc, out, _ = roda("--help")
        self.assertEqual(rc, 0)
        self.assertIn("ai-test-loop", out)
        self.assertIn("USO", out)
        self.assertIn("--config", out)
        self.assertIn("PRIMEIRA VEZ", out)
        # o usage do argparse do runner nao deve vazar
        self.assertNotIn("loop_runner.py [-h]", out)
        self.assertNotIn("show this help message and exit", out)

    def test_help_leva_para_a_loja(self):
        """O help e o degrau mais barato do funil: tem de ter o link."""
        _, out, _ = roda("--help")
        self.assertIn("skills.seu.pet/p/ai-test-loop", out)
        self.assertIn("utm_source=cli", out)

    def test_sem_argumento_mostra_ajuda_em_vez_de_erro(self):
        """Sem argumento o usuario via 'error: --config is required' e ia embora."""
        rc, out, err = roda()
        self.assertEqual(rc, 0, f"sem argumento deveria sair limpo, veio rc={rc}")
        self.assertIn("USO", out)
        self.assertNotIn("the following arguments are required", out + err)
        self.assertNotIn("usage: loop_runner.py", out + err)


class TestVersao(unittest.TestCase):
    def test_version_responde_a_versao_do_pacote(self):
        rc, out, err = roda("--version")
        self.assertEqual(rc, 0)
        with open(os.path.join(RAIZ, "package.json"), encoding="utf-8") as f:
            versao = json.load(f)["version"]
        self.assertEqual(out.strip(), versao, "a saida tem de ser SO a versao")
        # o Python nao pode rodar depois e imprimir o usage dele
        self.assertNotIn("loop_runner.py", out + err)
        self.assertNotIn("--config", out + err)


class TestRunReal(unittest.TestCase):
    def test_dry_run_lista_os_checks(self):
        rc, out, _ = roda("--config", os.path.join(RAIZ, "templates", "loop.example.json"),
                          "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("[dry-run]", out)
        self.assertIn("threshold=", out)

    def test_run_de_verdade_da_nota_e_gaps(self):
        """Um run real tem de produzir SCORE, GATE e GAPS — nao so ecoar."""
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "mini.json")
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({
                    "threshold": 50, "max_rounds": 1,
                    "checks": [
                        {"name": "passa", "cmd": "true", "weight": 50, "pass_when": "exit0"},
                        {"name": "falha", "cmd": "false", "weight": 50, "pass_when": "exit0"},
                    ],
                }, f)
            rc, out, _ = roda("--config", cfg)
            self.assertIn("SCORE=", out)
            self.assertIn("GATE=", out)
            self.assertIn("GAPS=", out)
            # 50 de 100: um passa, um falha, e o gap nomeia qual
            self.assertIn("SCORE=50", out)
            self.assertIn("falha", out.split("GAPS=")[1])


class TestPythonAusente(unittest.TestCase):
    def test_python_inexistente_da_erro_claro(self):
        """Sem python o usuario precisa saber o que instalar, nao ver stack trace."""
        rc, out, err = roda("--config", "x.json",
                            env_extra={"AI_TEST_LOOP_PYTHON": "/caminho/que/nao/existe"})
        self.assertNotEqual(rc, 0)
        juntos = out + err
        self.assertIn("Python 3", juntos)
        self.assertIn("AI_TEST_LOOP_PYTHON", juntos)


if __name__ == "__main__":
    unittest.main(verbosity=2)
