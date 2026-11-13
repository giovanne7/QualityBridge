"""Demonstra uma sugestão válida e uma sugestão rejeitada.

Execute a partir da raiz após instalar o projeto e as dependências de
desenvolvimento: ``python examples/auto-healing-demo.py``.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from qualitybridge.auto_healing import AutoHealingAnalyzer, FailureContext
from qualitybridge.controller import run_project
from qualitybridge.healing_validation import SuggestionValidator, ValidationRequest

RECENT_DIFF = """@@ -1 +1 @@
-BUTTON_ID = "login-button"
+BUTTON_ID = "signin-button"
"""


def main() -> None:
    directory = tempfile.mkdtemp(prefix="qualitybridge-auto-healing-demo-")
    try:
        root = Path(directory)
        (root / "app.py").write_text('BUTTON_ID = "signin-button"\n', encoding="utf-8")
        (root / "test_ui.py").write_text(
            "from app import BUTTON_ID\n\n"
            "def test_button_selector():\n"
            '    assert BUTTON_ID == "login-button"\n',
            encoding="utf-8",
        )
        config = root / "qualitybridge.yaml"
        config.write_text(
            "project: Auto-healing demo\nrunner: pytest\n"
            "timeout_seconds: 30\n"
            "env:\n  PYTEST_DISABLE_PLUGIN_AUTOLOAD: '1'\n"
            "suites:\n  full:\n    paths: [test_ui.py]\n",
            encoding="utf-8",
        )

        failed, _ = run_project(config)
        case = failed.cases[0]
        source = (root / "test_ui.py").read_text(encoding="utf-8")
        context = FailureContext(
            case=case,
            test_file="test_ui.py",
            test_source=source,
            related_source=(root / "app.py").read_text(encoding="utf-8"),
            recent_diff=RECENT_DIFF,
        )
        suggestion = AutoHealingAnalyzer().analyze(failed, [context]).suggestions[0]
        print("Falha inicial:", failed.status)
        print("Sugestão:")
        print(suggestion.diff)

        request = ValidationRequest(
            runner="pytest",
            test_selector="test_ui.py::test_button_selector",
            test_name="test_button_selector",
            python_executable=sys.executable,
            env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        )
        validated, outcome = SuggestionValidator().validate(suggestion, request, root)
        print("Validação válida:", validated.state, "-", outcome.message)

        rejected_suggestion = suggestion.model_copy(update={"new_text": "wrong-button"})
        rejected, rejected_outcome = SuggestionValidator().validate(
            rejected_suggestion, request, root
        )
        print("Validação rejeitada:", rejected.state, "-", rejected_outcome.message)
    finally:
        # O Windows pode manter o SQLite bloqueado por alguns instantes;
        # a demonstração não transforma essa limpeza em falha de validação.
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    main()
