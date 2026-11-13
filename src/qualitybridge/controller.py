import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException

from qualitybridge.adapters import factory_for
from qualitybridge.config import ConfigurationError, load_config
from qualitybridge.history import HistoryStore
from qualitybridge.jest import read_jest
from qualitybridge.junit import read_junit
from qualitybridge.models import ExecutionContext, RunResult
from qualitybridge.process import execute
from qualitybridge.reports import write_reports
from qualitybridge.strategies import select_suite


def run_project(
    config_path: Path,
    suite_name: str = "full",
    use_docker: bool = False,
    timeout: int | None = None,
) -> tuple[RunResult, Path]:
    config, root, output = load_config(config_path)
    suite = select_suite(config, suite_name)
    if use_docker and config.docker is None:
        raise ConfigurationError("Defina docker.image no YAML para usar --docker.")
    started_at = datetime.now(UTC)
    run_id = started_at.strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12]
    directory = output / "runs" / run_id
    directory.mkdir(parents=True)
    adapter = factory_for(config.runner).create_adapter()

    if config.runner == "jest":
        report = directory / "raw-jest.json"
    else:
        report = directory / "raw-junit.xml"

    target_report = Path("/qualitybridge-output") / report.name if use_docker else report

    # POSIX container paths must not be normalized using the Windows host's separators.
    runner_command = adapter.command(config, suite, root, target_report, use_docker)
    runner_env = {**config.env, **adapter.environment(target_report)}
    if use_docker:
        runner_command = [
            arg.replace("\\", "/") if "qualitybridge-output" in arg else arg
            for arg in runner_command
        ]
        runner_env = {
            k: v.replace("\\", "/") if k == "PLAYWRIGHT_JUNIT_OUTPUT_FILE" else v
            for k, v in runner_env.items()
        }
    env = {**os.environ, **runner_env}
    node_version = None
    runner_version = None
    python_version = None if use_docker else sys.version.split()[0]
    if not use_docker and config.runner == "pytest":
        try:
            python_executable = config.python or sys.executable
            python_result = subprocess.run(
                [python_executable, "--version"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
                cwd=root,
            )
            python_version = (
                python_result.stdout or python_result.stderr
            ).strip() or python_version
            runner_result = subprocess.run(
                [python_executable, "-m", "pytest", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                cwd=root,
            )
            runner_version = (runner_result.stdout or runner_result.stderr).strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass
    elif not use_docker and config.runner in ("playwright", "jest"):
        try:
            node_version = (
                subprocess.run(
                    [config.node, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                    cwd=root,
                ).stdout.strip()
                or None
            )
            package = (
                root / "node_modules" / "@playwright" / "test" / "package.json"
                if config.runner == "playwright"
                else root / "node_modules" / "jest" / "package.json"
            )
            runner_version = json.loads(package.read_text(encoding="utf-8")).get("version")
        except (OSError, subprocess.TimeoutExpired):
            pass
        except (json.JSONDecodeError, AttributeError, UnicodeError):
            pass
    command = runner_command
    container_name = "qualitybridge-" + run_id.lower()
    if use_docker:
        # Values travel through the environment, not the process command line or report.
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            config.docker.network,
            "--init",
            "--mount",
            f"type=bind,source={root},target=/project",
            "--mount",
            f"type=bind,source={directory},target=/qualitybridge-output",
            "--workdir",
            "/project",
        ]
        for key in runner_env:
            command += ["--env", key]
        command += [config.docker.image, *runner_command]
    start = time.monotonic()
    process = execute(
        command, root, env, directory / "execution.log", timeout or config.timeout_seconds
    )
    diagnostics = []
    if use_docker and (process.timed_out or process.cancelled):
        try:
            cleanup = subprocess.run(
                ["docker", "rm", "--force", container_name],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if cleanup.returncode:
                diagnostics.append(
                    "Não foi possível confirmar a remoção do container: "
                    + cleanup.stderr.decode(errors="replace")
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            diagnostics.append(f"Falha ao remover container {container_name}: {exc}")
    cases = []
    report_error = False

    try:
        if config.runner == "jest":
            cases = read_jest(report)
        else:
            cases = read_junit(report)

    except (OSError, ValueError, TypeError, ParseError, DefusedXmlException) as exc:
        # A broken external report must still produce a diagnostic JSON/HTML/JUnit report.
        report_error = True
        diagnostics.append(f"Relatório de testes ausente ou inválido: {exc}")
    if process.timed_out:
        status = "timeout"
        diagnostics.append(
            f"Tempo limite de {timeout or config.timeout_seconds} segundos excedido."
        )
    elif process.cancelled:
        status = "cancelled"
        diagnostics.append("Execução interrompida pelo usuário.")
    elif process.error:
        status = "error"
        diagnostics.append(f"Não foi possível iniciar o executor: {process.error}")
    elif config.runner == "pytest" and process.exit_code == 5:
        status = "no_tests"
        diagnostics.append("Nenhum teste foi selecionado pelo pytest.")
    elif config.runner == "jest" and not cases and process.exit_code == 1:
        status = "no_tests"
        diagnostics.append("Nenhum teste foi selecionado pelo Jest.")
    elif process.exit_code not in (0, 1) or report_error:
        status = "error"
        diagnostics.append(
            f"Executor terminou com código {process.exit_code}. Consulte execution.log."
        )
    elif any(c.status == "error" for c in cases):
        status = "error"
        diagnostics.append("O executor informou erros de coleta, preparação ou execução de testes.")
    elif any(c.status == "failed" for c in cases):
        status = "failed"
    elif process.exit_code != 0:
        status = "error"
        diagnostics.append("O executor terminou com erro sem registrar um teste reprovado.")
    elif not cases:
        status = "no_tests"
        diagnostics.append("Nenhum caso de teste consta no relatório.")
    else:
        status = "passed"
    result = RunResult(
        id=run_id,
        project=config.project,
        runner=config.runner,
        suite=suite_name,
        started_at=started_at,
        duration_seconds=time.monotonic() - start,
        status=status,
        exit_code=process.exit_code,
        command=command,
        context=ExecutionContext(
            platform=platform.platform(),
            python_version=python_version,
            python_executable=(
                config.python or sys.executable if config.runner == "pytest" else None
            ),
            node_executable=config.node if config.runner in ("playwright", "jest") else None,
            node_version=node_version,
            runner_version=runner_version,
            docker=use_docker,
            docker_image=config.docker.image if use_docker and config.docker else None,
            docker_network=config.docker.network if use_docker and config.docker else None,
            configured_env_keys=sorted(config.env),
            timeout_seconds=timeout or config.timeout_seconds,
        ),
        cases=cases,
        diagnostics=diagnostics,
    )
    quarantine_reasons = HistoryStore(output / "history.sqlite3").quarantine_reasons(
        config.project, config.runner, suite_name
    )
    if quarantine_reasons:
        result.cases = [
            case.model_copy(
                update={
                    "quarantined": (case.classname, case.name) in quarantine_reasons,
                    "quarantine_reason": quarantine_reasons.get((case.classname, case.name), ""),
                }
            )
            for case in result.cases
        ]
    write_reports(result, directory)
    try:
        HistoryStore(output / "history.sqlite3").save(result)
    except (sqlite3.Error, OSError) as exc:
        result.diagnostics.append(f"Não foi possível salvar o histórico: {exc}")
        write_reports(result, directory)
    return result, directory
