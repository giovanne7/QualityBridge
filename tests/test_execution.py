import json
import os
import sys

import pytest
import yaml
from typer.testing import CliRunner

from qualitybridge.cli import app
from qualitybridge.controller import run_project
from qualitybridge.history import HistoryStore
from qualitybridge.process import execute


def project(tmp_path, code, **options):
    (tmp_path / "test_sample.py").write_text(code, encoding="utf-8")
    config = {
        "project": "Integração",
        "runner": "pytest",
        "timeout_seconds": 30,
        "env": {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        **options,
    }
    path = tmp_path / "qualitybridge.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def test_real_pytest_reports_history_and_unique_runs(tmp_path):
    path = project(tmp_path, "def test_ok():\n    assert 2 + 2 == 4\n")
    first, first_dir = run_project(path)
    second, second_dir = run_project(path)
    assert first.status == second.status == "passed"
    assert first_dir != second_dir
    assert first.counts["passed"] == 1
    for file in ("report.json", "report.html", "junit.xml", "execution.log", "raw-junit.xml"):
        assert (first_dir / file).is_file()
    assert json.loads((first_dir / "report.json").read_text(encoding="utf-8"))["status"] == "passed"
    rows = HistoryStore(tmp_path / ".qualitybridge/history.sqlite3").recent("Integração")
    assert [r.id for r in rows] == [second.id, first.id]


def test_assertion_failure_and_html_escaping(tmp_path):
    path = project(tmp_path, 'def test_bad():\n    assert False, "<script>alert(1)</script>"\n')
    result, directory = run_project(path)
    assert result.status == "failed"
    assert result.counts["failed"] == 1
    html = (directory / "report.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_no_tests_is_not_success(tmp_path):
    result, _ = run_project(project(tmp_path, "value = 1\n"))
    assert result.status == "no_tests"


def test_collection_error_is_not_test_failure(tmp_path):
    result, _ = run_project(project(tmp_path, "raise RuntimeError('collection failed')\n"))
    assert result.status == "error"


def test_timeout_generates_reports(tmp_path):
    result, directory = run_project(
        project(tmp_path, "import time\ndef test_slow():\n    time.sleep(60)\n"), timeout=1
    )
    assert result.status == "timeout"
    assert (directory / "report.json").exists()


def test_missing_executable_generates_reports(tmp_path):
    result, directory = run_project(project(tmp_path, "", python="qualitybridge-missing-python"))
    assert result.status == "error"
    assert result.exit_code is None
    assert (directory / "execution.log").exists()


def test_quick_suite_selects_subset(tmp_path):
    path = project(
        tmp_path,
        "def test_fast():\n    pass\ndef test_slow():\n    assert False\n",
        suites={"full": {}, "quick": {"paths": ["test_sample.py::test_fast"]}},
    )
    result, _ = run_project(path, "quick")
    assert result.status == "passed"
    assert len(result.cases) == 1


def test_cli_never_overwrites_config(tmp_path):
    path = tmp_path / "qualitybridge.yaml"
    path.write_text("existing", encoding="utf-8")
    result = CliRunner().invoke(app, ["init", "--config", str(path)])
    assert result.exit_code == 2
    assert path.read_text() == "existing"


@pytest.mark.parametrize("body,expected", [("assert True", 0), ("assert False", 1)])
def test_cli_exit_codes(tmp_path, body, expected):
    path = project(tmp_path, f"def test_result():\n    {body}\n")
    result = CliRunner().invoke(app, ["run", "-c", str(path)])
    assert result.exit_code == expected, result.output


def test_process_does_not_interpret_shell_metacharacters(tmp_path):
    log = tmp_path / "log.txt"
    argument = "a; echo SHOULD_NOT_EXECUTE & $(anything)"
    result = execute(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", argument],
        tmp_path,
        dict(os.environ),
        log,
        10,
    )
    assert result.exit_code == 0
    assert log.read_text().strip() == argument
