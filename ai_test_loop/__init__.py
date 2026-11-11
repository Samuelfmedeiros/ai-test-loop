"""ai-test-loop — deterministic critic for AI agents.

Este pacote é um casco fino em volta do mesmo `loop_runner.py` que o pacote npm usa (lá em
versão Node). O contrato é idêntico: `SCORE=` / `GAPS=` / `EVIDENCE=` e exit code 0/1 para CI.

Por que o casco existe: `pip install ai-test-loop` precisa dar o mesmo comando que
`npx ai-test-loop`, senão o produto tem dois nomes e as estatísticas ficam partidas.
"""

__version__ = "1.1.0"
