"""Console entry point for `ai-test-loop`.

Delega ao `loop_runner.py`, que continua sendo a implementação canônica em Python. Nada de
lógica duplicada aqui: se o runner mudar, este arquivo não precisa mudar.
"""

import os
import sys

from . import __version__


def _runner_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "loop_runner.py")


def main() -> int:
    argv = sys.argv[1:]

    # --version e --help do PRODUTO, não do argparse interno: quem digita
    # `ai-test-loop --version` quer a versão do pacote.
    if "--version" in argv or "-V" in argv:
        print(__version__)
        return 0
    if not argv or "--help" in argv or "-h" in argv:
        print(
            f"ai-test-loop {__version__} — nota 0-100 no seu agente antes de entregar.\n"
            "\n"
            "USO\n"
            "  ai-test-loop --config <arquivo.json>       roda as checagens e imprime a nota\n"
            "  ai-test-loop --config <arq> --dry-run      mostra o que rodaria, sem executar\n"
            "  ai-test-loop --config <arq> --quiet        só SCORE/GAPS/EVIDENCE\n"
            "  ai-test-loop --config <arq> --json         relatório completo em JSON\n"
            "  ai-test-loop --show-state                  última rodada e placar guardados\n"
            "  ai-test-loop --version                     versão do pacote\n"
            "\n"
            "Pacote completo, packs e suporte: "
            "https://skills.seu.pet/p/ai-test-loop"
            "?utm_source=cli&utm_medium=help&utm_campaign=ai-test-loop\n"
        )
        return 0

    runner = _runner_path()
    if not os.path.exists(runner):
        sys.stderr.write(f"ai-test-loop: runner não encontrado em {runner}\n")
        return 2

    # exec em vez de subprocess: o exit code do runner (0/1/2) é o exit code do comando,
    # que é exatamente o que o CI consome.
    os.execv(sys.executable, [sys.executable, runner, *argv])
    return 2  # inalcançável


if __name__ == "__main__":
    raise SystemExit(main())
