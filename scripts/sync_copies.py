#!/usr/bin/env python3
"""Espelha o runner CANONICO para as copias distribuidas do repo.

Por que isto existe (medido 15/11/2026): o repo tinha 3 copias de
`loop_runner.py` com hashes diferentes —

    scripts/loop_runner.py               (CANONICA — e' o que package.json
                                          publica em "files"; mtime mais novo)
    skills/ai-test-loop/scripts/...      (defasada 2 dias)
    ai_test_loop/scripts/...             (defasada 4 dias, era o layout pip)

Tres copias com conteudos diferentes significa que o bug corrigido na canonica
continua vivo em quem instalou o pacote. O `scripts/` e' a fonte unica: e' o
diretorio que vai pro npm (`package.json > files`) e o que os testes exercitam.

Uso:
    python scripts/sync_copies.py            # espelha e mostra
    python scripts/sync_copies.py --check    # rc=1 se divergir, sem escrever
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import shutil
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CANONICO = RAIZ / "scripts"
ALVOS = [
    RAIZ / "skills" / "ai-test-loop" / "scripts",
    RAIZ / "ai_test_loop" / "scripts",
]
ARQUIVOS = ["loop_runner.py", "loop_runner.js", "gate_core.py"]


def _sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "(ausente)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="nao escreve; rc=1 se alguma copia divergir")
    args = ap.parse_args()

    divergencias = []
    for nome in ARQUIVOS:
        fonte = CANONICO / nome
        if not fonte.exists():
            print(f"[skip] {nome}: nao existe na canonica {CANONICO}")
            continue
        h_fonte = _sha(fonte)
        for alvo_dir in ALVOS:
            alvo = alvo_dir / nome
            h_alvo = _sha(alvo)
            estado = "ok" if h_alvo == h_fonte else "DIVERGE"
            if h_alvo != h_fonte:
                divergencias.append((nome, alvo, h_alvo, h_fonte))
            print(f"[{estado:8}] {nome:16} {alvo.relative_to(RAIZ)} "
                  f"{h_alvo} -> {h_fonte}")

    if args.check:
        if divergencias:
            print(f"\n{len(divergencias)} copia(s) divergente(s) — rode sem --check "
                  f"para espelhar da canonica.")
            return 1
        print("\ntodas as copias identicas a canonica.")
        return 0

    for _nome, alvo, _h_alvo, _h_fonte in divergencias:
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CANONICO / _nome, alvo)
        print(f"[escrito] {alvo.relative_to(RAIZ)} = {_h_fonte}")
    if not divergencias:
        print("nada a fazer: copias ja identicas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
