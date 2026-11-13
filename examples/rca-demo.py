"""Demonstra a análise de causa raiz em um repositório Git temporário.

Execute a partir da raiz após instalar o projeto e as dependências de
desenvolvimento: ``python examples/rca-demo.py``. Para incluir o LLM configurado
(veja docs/rca.md): ``python examples/rca-demo.py --llm``.

Roteiro:
1. cria um projeto com testes que passam e faz o commit inicial;
2. executa a suíte (aprovada) e registra o histórico;
3. faz um commit que introduz um defeito em ``divide``;
4. executa novamente (reprovada) e pede o diagnóstico ao RCA.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from qualitybridge.controller import run_project
from qualitybridge.history import HistoryStore
from qualitybridge.llm import client_from_env
from qualitybridge.rca import GitHistory, RootCauseAnalyzer


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def main() -> None:
    if shutil.which("git") is None:
        raise SystemExit("Git não encontrado no PATH.")
    use_llm = "--llm" in sys.argv
    directory = tempfile.mkdtemp(prefix="qualitybridge-rca-demo-")
    try:
        root = Path(directory)
        (root / "src").mkdir()
        (root / "tests").mkdir()
        (root / "src" / "calc.py").write_text(
            "def divide(a, b):\n    return a / b\n\n\ndef total(items):\n    return sum(items)\n",
            encoding="utf-8",
        )
        (root / "tests" / "test_calc.py").write_text(
            "import sys\nfrom pathlib import Path\n\n"
            "sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))\n\n"
            "from calc import divide, total  # noqa: E402\n\n\n"
            "def test_divide():\n    assert divide(9, 3) == 3\n\n\n"
            "def test_total():\n    assert total([1, 2, 3]) == 6\n",
            encoding="utf-8",
        )
        (root / ".gitignore").write_text(".qualitybridge/\n", encoding="utf-8")
        config = root / "qualitybridge.yaml"
        config.write_text(
            "project: RCA demo\nrunner: pytest\ntimeout_seconds: 60\n"
            "env:\n  PYTEST_DISABLE_PLUGIN_AUTOLOAD: '1'\n"
            "suites:\n  full:\n    paths: [tests]\n",
            encoding="utf-8",
        )
        git(root, "init", "-q")
        git(root, "config", "user.email", "demo@example.com")
        git(root, "config", "user.name", "Demo")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "versão inicial da calculadora")

        first, _ = run_project(config)
        print(f"1ª execução: {first.status}")

        (root / "src" / "calc.py").write_text(
            "def divide(a, b):\n    return a // b + 1\n\n\ndef total(items):\n"
            "    return sum(items)\n",
            encoding="utf-8",
        )
        git(root, "commit", "-qam", "otimiza divisão inteira")

        second, run_directory = run_project(config)
        print(f"2ª execução: {second.status}")

        history = HistoryStore(root / ".qualitybridge" / "history.sqlite3").recent("RCA demo", 50)
        llm = client_from_env() if use_llm else None
        if use_llm and llm is None:
            print("Nenhum LLM configurado; seguindo só com a heurística.")
        report = RootCauseAnalyzer(GitHistory(root), llm=llm, history=history).analyze(second)
        (run_directory / "rca.json").write_text(report.model_dump_json(indent=2), "utf-8")

        for diagnosis in report.diagnoses:
            print(f"\nTeste: {diagnosis.classname}::{diagnosis.test_name} ({diagnosis.engine})")
            for hypothesis in diagnosis.hypotheses:
                print(
                    f"  - {hypothesis.category} ({hypothesis.confidence:.0%}): "
                    f"{hypothesis.cause} [{', '.join(hypothesis.evidence_ids)}]"
                )
            print("  Evidências:")
            for item in diagnosis.evidence:
                first_line = item.content.splitlines()[0]
                print(f"    {item.id} [{item.kind}] {first_line}")
            if diagnosis.primary:
                print("  Sugestões:")
                for fix in diagnosis.primary.suggested_fix:
                    print(f"    * {fix}")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    main()
