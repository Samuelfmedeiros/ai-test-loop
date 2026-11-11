"""Regressao: o runner Python nao pode exigir bash.

Medido em 11/11/2026: `pip install ai-test-loop` em maquina sem bash (container minimo,
alpine, PATH magro) morria com FileNotFoundError traceback em vez de rodar o check.
O runner Node tinha o mesmo defeito e caia para cmd.exe no POSIX — corrigido em 1.1.1.

Este teste roda o runner com PATH sem bash e exige que o check execute.
"""

import os
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(RAIZ, "ai_test_loop", "scripts", "loop_runner.py")


def _run(env_patho: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "loop.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write('{"name":"sem-bash","threshold":85,'
                     '"checks":[{"name":"ok","cmd":"echo rodou sem bash","weight":100}]}')
        env = {"PATH": env_patho, "HOME": tmp, "TERM": "dumb",
               "AI_TEST_LOOP_NO_TELEMETRY": "1"}
        return subprocess.run(
            [sys.executable, RUNNER, "--config", cfg],
            capture_output=True, text=True, env=env, timeout=120,
        )


class TestSemBash(unittest.TestCase):
    def test_runner_roda_sem_bash_no_path(self):
        """POSIX sem bash: age pelo /bin/sh, nao estoura FileNotFoundError."""
        if os.name == "nt":
            self.skipTest("regra vale para POSIX")
        with tempfile.TemporaryDirectory() as vazio:
            proc = _run(vazio)
        juntos = (proc.stdout or "") + (proc.stderr or "")
        self.assertNotIn("FileNotFoundError", juntos,
                         f"runner exigiu bash e quebrou:\n{juntos[-600:]}")
        self.assertNotIn("No such file or directory: 'bash'", juntos)
        self.assertIn("SCORE=100.0", juntos, juntos[-600:])
        self.assertIn("GATE=passed", juntos, juntos[-600:])
        self.assertEqual(proc.returncode, 0, juntos[-600:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
