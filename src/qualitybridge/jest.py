
import json
import re
from pathlib import Path

from qualitybridge.models import TestCase


def split_failure(text: str) -> tuple[str, str]:
    """Separa a mensagem de erro do stack trace."""
    text = text.strip()
    lines = text.splitlines()

    stack_start = next(
        (i for i, line in enumerate(lines) if re.match(r"^\s*at\s+", line)),
        None,
    )

    if stack_start is None:
        return text, ""

    message = "\n".join(lines[:stack_start]).strip()
    stack_trace = "\n".join(lines[stack_start:]).strip()

    return message or text, stack_trace


def read_jest(path: Path) -> list[TestCase]:
    """Converte o relatório JSON do Jest para o modelo comum do QualityBridge."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Relatório JSON do Jest inválido: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("testResults"), list):
        raise TypeError("O arquivo não contém um relatório JSON válido do Jest.")

    cases = []

    status_map = {
        "passed": "passed",
        "failed": "failed",
        "pending": "skipped",
        "todo": "skipped",
        "disabled": "skipped",
    }

    for suite in data["testResults"]:
        if not isinstance(suite, dict):
            raise TypeError("Resultado de suíte inválido no relatório do Jest.")

        assertions = suite.get("assertionResults", [])

        if not isinstance(assertions, list):
            raise TypeError("Lista de testes inválida no relatório do Jest.")

        for assertion in assertions:
            if not isinstance(assertion, dict):
                raise TypeError("Resultado de teste inválido no relatório do Jest.")

            raw_status = assertion.get("status")
            if raw_status not in status_map:
                raise ValueError(f"Estado de teste desconhecido no Jest: {raw_status}")

            failures = assertion.get("failureMessages") or []
            failure_text = "\n".join(str(message) for message in failures)

            message, stack_trace = split_failure(failure_text)

            duration_ms = assertion.get("duration") or 0

            cases.append(
                TestCase(
                    name=assertion.get("fullName") or assertion.get("title", "sem nome"),
                    classname=suite.get("name") or suite.get("testFilePath", ""),
                    file_path=suite.get("testFilePath") or suite.get("name", ""),
                    status=status_map[raw_status],
                    duration_seconds=float(duration_ms) / 1000,
                    message=message,
                    stack_trace=stack_trace,
                )
            )

        # Erros de inicialização ou execução da suíte sem casos individuais.
        if not assertions and suite.get("status") == "failed":
            failure_text = suite.get("failureMessage") or suite.get("message") or ""
            message, stack_trace = split_failure(str(failure_text))

            cases.append(
                TestCase(
                    name="Erro da suíte",
                    classname=suite.get("name") or suite.get("testFilePath", ""),
                    file_path=suite.get("testFilePath") or suite.get("name", ""),
                    status="error",
                    message=message or "Falha na execução da suíte Jest.",
                    stack_trace=stack_trace,
                )
            )

    return cases
