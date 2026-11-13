import json
from pathlib import Path

import pytest

from qualitybridge import controller
from qualitybridge.config import ConfigurationError
from qualitybridge.history import HistoryStore
from qualitybridge.process import ProcessResult


def configure(tmp_path, extra=""):
    path = tmp_path / "qualitybridge.yaml"
    path.write_text("project: Test\nrunner: pytest\n" + extra, encoding="utf-8")
    return path


def test_success_exit_without_report_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(controller, "execute", lambda *args: ProcessResult(0))
    result, directory = controller.run_project(configure(tmp_path))
    assert result.status == "error"
    assert "ausente ou inválido" in result.diagnostics[0]
    assert (directory / "junit.xml").exists()


def test_cancelled_run_still_has_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(controller, "execute", lambda *args: ProcessResult(-1, cancelled=True))
    result, directory = controller.run_project(configure(tmp_path))
    assert result.status == "cancelled"
    assert json.loads((directory / "report.json").read_text())["status"] == "cancelled"


def test_docker_needs_explicit_image(tmp_path):
    with pytest.raises(ConfigurationError, match="docker.image"):
        controller.run_project(configure(tmp_path), use_docker=True)
    assert not (tmp_path / ".qualitybridge").exists()


def test_docker_timeout_cleans_named_container_and_does_not_log_secrets(tmp_path, monkeypatch):
    calls = []

    def execute(command, cwd, env, log, timeout):
        calls.append(command)
        assert env["TEST_TOKEN"] == "secret-value"
        return ProcessResult(1, timed_out=True)

    def cleanup(command, **kwargs):
        calls.append(command)
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(controller, "execute", execute)
    monkeypatch.setattr(controller.subprocess, "run", cleanup)
    path = configure(
        tmp_path, "docker:\n  image: qualitybridge:local\nenv:\n  TEST_TOKEN: secret-value\n"
    )
    result, directory = controller.run_project(path, use_docker=True)
    assert result.status == "timeout"
    assert calls[0][:3] == ["docker", "run", "--rm"]
    assert "--junitxml=/qualitybridge-output/raw-junit.xml" in calls[0]
    assert "none" in calls[0]
    assert calls[1] == ["docker", "rm", "--force", "qualitybridge-" + result.id.lower()]
    assert "secret-value" not in (directory / "report.json").read_text()


def test_quarantine_is_visible_in_html_json_and_junit(tmp_path, monkeypatch):
    config_path = configure(tmp_path)
    HistoryStore(tmp_path / ".qualitybridge" / "history.sqlite3").quarantine(
        "Test",
        "pytest",
        "full",
        "tests.test_sample",
        "test_flaky",
        "quarantine",
        "Falha intermitente confirmada",
        "responsavel",
    )

    def execute(command, cwd, env, log, timeout):
        report = next(
            Path(arg.split("=", 1)[1]) for arg in command if arg.startswith("--junitxml=")
        )
        report.write_text(
            '<testsuite><testcase classname="tests.test_sample" name="test_flaky"/></testsuite>',
            encoding="utf-8",
        )
        return ProcessResult(0)

    monkeypatch.setattr(controller, "execute", execute)

    result, directory = controller.run_project(config_path)

    assert result.cases[0].quarantined is True
    assert result.cases[0].quarantine_reason == "Falha intermitente confirmada"
    assert "Em quarentena: Falha intermitente confirmada" in (directory / "report.html").read_text()
    assert json.loads((directory / "report.json").read_text())["cases"][0]["quarantined"] is True
    assert 'name="qualitybridge.quarantined" value="true"' in (directory / "junit.xml").read_text()
