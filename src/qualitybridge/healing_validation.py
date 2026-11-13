"""Validação isolada de sugestões de Auto-healing.

O validador usa somente comandos construídos internamente para runners
conhecidos. A cópia temporária reduz efeitos permanentes, mas não substitui um
container ou sandbox para executar código de projeto não confiável.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Literal
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException
from pydantic import Field, field_validator

from qualitybridge.auto_healing import HealingSuggestion, apply_validation_state
from qualitybridge.jest import read_jest
from qualitybridge.junit import read_junit
from qualitybridge.models import StrictModel, TestCase
from qualitybridge.process import execute

ValidationState = Literal["validated", "rejected", "inconclusive"]


class ValidationRequest(StrictModel):
    """Parâmetros declarativos para um comando autorizado de teste."""

    runner: Literal["pytest", "playwright", "jest"]
    test_selector: str
    test_name: str = ""
    python_executable: str = sys.executable
    node_executable: str = "node"
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("test_selector", "test_name")
    @classmethod
    def safe_selector(cls, value: str) -> str:
        if "\x00" in value or value.startswith("-"):
            raise ValueError("O seletor de validação é inválido.")
        return value

    @field_validator("python_executable")
    @classmethod
    def approved_python(cls, value: str) -> str:
        if Path(value).name.lower() not in {"python", "python.exe", "py", "py.exe"}:
            raise ValueError("O executável de Python não é autorizado para validação.")
        return value

    @field_validator("node_executable")
    @classmethod
    def approved_node(cls, value: str) -> str:
        if Path(value).name.lower() not in {"node", "node.exe"}:
            raise ValueError("O executável Node não é autorizado para validação.")
        return value


class ValidationOutcome(StrictModel):
    state: ValidationState
    message: str
    exit_code: int | None = None
    preserved_original: bool = True
    executed_case: str = ""


def _safe_relative_path(root: Path, relative: str) -> Path:
    if not relative or "\x00" in relative:
        raise ValueError("O caminho da sugestão não pode ser vazio ou conter NUL.")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("A sugestão aponta para fora da cópia temporária.")
    return candidate


def _reject_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("A validação não aceita projetos com links simbólicos.")


def _authorized_command(
    request: ValidationRequest, temp_root: Path, report: Path
) -> tuple[list[str], dict[str, str]]:
    if request.runner == "pytest":
        command = [
            request.python_executable,
            "-B",
            "-m",
            "pytest",
            f"--junitxml={report}",
            "-o",
            "junit_family=xunit2",
            request.test_selector,
        ]
        environment = {**os.environ, **request.env}
    elif request.runner == "playwright":
        command = [
            request.node_executable,
            "node_modules/@playwright/test/cli.js",
            "test",
            "--reporter=junit",
            request.test_selector,
        ]
        environment = {
            **os.environ,
            **request.env,
            "PLAYWRIGHT_JUNIT_OUTPUT_FILE": str(report),
            "PLAYWRIGHT_JUNIT_STRIP_ANSI": "true",
        }
    else:
        command = [
            request.node_executable,
            "node_modules/jest/bin/jest.js",
            "--json",
            f"--outputFile={report}",
            "--runTestsByPath",
            request.test_selector,
        ]
        if request.test_name:
            command += ["--testNamePattern", request.test_name]
        environment = {**os.environ, **request.env}

    # temp_root is deliberately used as cwd by the caller; no caller-provided
    # cwd or shell fragment is accepted by this API.
    return command, environment


def _read_cases(request: ValidationRequest, report: Path) -> list[TestCase]:
    if request.runner == "jest":
        return read_jest(report)
    return read_junit(report)


def _find_target(request: ValidationRequest, cases: list[TestCase]) -> TestCase | None:
    names = {request.test_name, request.test_selector.rsplit("::", 1)[-1]}
    names.discard("")
    for case in cases:
        if case.name in names:
            return case
    return None


class SuggestionValidator:
    """Aplica e valida uma sugestão somente dentro de uma cópia descartável."""

    def validate(
        self,
        suggestion: HealingSuggestion,
        request: ValidationRequest,
        project_root: Path,
    ) -> tuple[HealingSuggestion, ValidationOutcome]:
        root = project_root.resolve()
        if not root.is_dir():
            raise ValueError(f"Projeto não encontrado: {root}")
        if not suggestion.file_path or not suggestion.old_text:
            outcome = ValidationOutcome(
                state="inconclusive",
                message="A sugestão não contém uma alteração concreta validável.",
            )
            return apply_validation_state(suggestion, outcome.state, outcome.message), outcome

        original_file = _safe_relative_path(root, suggestion.file_path)
        if not original_file.is_file():
            outcome = ValidationOutcome(
                state="inconclusive",
                message=f"Arquivo da sugestão não encontrado: {suggestion.file_path}",
            )
            return apply_validation_state(suggestion, outcome.state, outcome.message), outcome

        original_bytes = original_file.read_bytes()
        original_text = original_bytes.decode("utf-8")
        if original_text.count(suggestion.old_text) != 1:
            outcome = ValidationOutcome(
                state="rejected",
                message="A alteração não é única no arquivo original; aplicação ambígua rejeitada.",
            )
            return apply_validation_state(suggestion, outcome.state, outcome.message), outcome

        _reject_symlinks(root)
        with tempfile.TemporaryDirectory(prefix="qualitybridge-healing-") as directory:
            temp_root = Path(directory) / "project"
            shutil.copytree(
                root,
                temp_root,
                symlinks=False,
                ignore=shutil.ignore_patterns(
                    ".git", ".qualitybridge", "__pycache__", ".pytest_cache"
                ),
            )
            temp_file = _safe_relative_path(temp_root, suggestion.file_path)
            updated = original_text.replace(suggestion.old_text, suggestion.new_text, 1)
            temp_file.write_text(updated, encoding="utf-8")

            selected_path = request.test_selector.split("::", 1)[0]
            if not _safe_relative_path(temp_root, selected_path).exists():
                raise ValueError("O seletor aponta para um arquivo ou diretório inexistente.")

            artifact_dir = temp_root / ".qualitybridge-healing"
            artifact_dir.mkdir()
            report_name = "raw-jest.json" if request.runner == "jest" else "raw-junit.xml"
            report = artifact_dir / report_name
            log = artifact_dir / "validation.log"
            command, environment = _authorized_command(request, temp_root, report)
            process = execute(command, temp_root, environment, log, request.timeout_seconds)

            if process.timed_out or process.cancelled or process.error:
                detail = process.error or "tempo limite ou cancelamento durante a validação"
                if log.exists():
                    detail += "\n" + log.read_text(encoding="utf-8", errors="replace")[-2000:]
                outcome = ValidationOutcome(
                    state="inconclusive",
                    message=detail,
                    exit_code=process.exit_code,
                )
            elif not report.is_file():
                detail = "O runner não produziu um relatório para confirmar o caso-alvo."
                if log.exists():
                    detail += "\n" + log.read_text(encoding="utf-8", errors="replace")[-2000:]
                outcome = ValidationOutcome(
                    state="inconclusive",
                    message=detail,
                    exit_code=process.exit_code,
                )
            else:
                try:
                    cases = _read_cases(request, report)
                except (OSError, ValueError, TypeError, ParseError, DefusedXmlException) as exc:
                    outcome = ValidationOutcome(
                        state="inconclusive",
                        message=f"Relatório de validação inválido: {exc}",
                        exit_code=process.exit_code,
                    )
                else:
                    target = _find_target(request, cases)
                    if target is None:
                        outcome = ValidationOutcome(
                            state="inconclusive",
                            message="O caso-alvo não apareceu no relatório de validação.",
                            exit_code=process.exit_code,
                        )
                    elif target.status == "passed" and process.exit_code == 0:
                        outcome = ValidationOutcome(
                            state="validated",
                            message="O caso-alvo passou após a alteração na cópia temporária.",
                            exit_code=process.exit_code,
                            executed_case=target.name,
                        )
                    elif target.status == "skipped":
                        outcome = ValidationOutcome(
                            state="rejected",
                            message="O caso-alvo foi ignorado; isso não valida a correção.",
                            exit_code=process.exit_code,
                            executed_case=target.name,
                        )
                    else:
                        detail = target.message or target.status
                        if target.stack_trace:
                            detail += "\n" + target.stack_trace
                        outcome = ValidationOutcome(
                            state="rejected",
                            message=f"O caso-alvo continua falhando: {detail}",
                            exit_code=process.exit_code,
                            executed_case=target.name,
                        )

        preserved = original_file.read_bytes() == original_bytes
        if not preserved:
            raise RuntimeError("A validação detectou alteração no arquivo original.")
        outcome = outcome.model_copy(update={"preserved_original": preserved})
        return (
            apply_validation_state(
                suggestion,
                outcome.state,
                outcome.message,
                outcome.exit_code,
            ),
            outcome,
        )
