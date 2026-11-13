from qualitybridge.auto_healing import AutoHealingAnalyzer, FailureContext
from qualitybridge.models import RunResult
from qualitybridge.models import TestCase as NormalizedTestCase


def result_for(case: NormalizedTestCase) -> RunResult:
    return RunResult(
        id="run-auto-healing",
        project="fixture",
        runner="pytest",
        suite="full",
        started_at="2026-01-01T00:00:00Z",
        duration_seconds=0.1,
        status="failed",
        exit_code=1,
        command=["pytest"],
        cases=[case],
    )


def context(case: NormalizedTestCase, source: str, diff: str) -> FailureContext:
    return FailureContext(
        case=case,
        test_file="tests/test_ui.py",
        test_source=source,
        related_source="BUTTON_ID = 'signin-button'",
        recent_diff=diff,
    )


def test_generates_concrete_selector_suggestion_and_diff():
    case = NormalizedTestCase(
        name="test_button_selector",
        classname="test_ui",
        status="failed",
        message="selector #login-button was not found",
    )
    source = 'def test_button_selector():\n    assert BUTTON_ID == "login-button"\n'
    diff = """@@ -1 +1 @@
-BUTTON_ID = "login-button"
+BUTTON_ID = "signin-button"
"""

    suggestion = (
        AutoHealingAnalyzer()
        .analyze(result_for(case), [context(case, source, diff)])
        .suggestions[0]
    )

    assert suggestion.classification == "outdated_test"
    assert suggestion.problem_type == "selector_changed"
    assert suggestion.state == "proposed"
    assert suggestion.old_text == "login-button"
    assert suggestion.new_text == "signin-button"
    assert "login-button" in suggestion.diff
    assert "signin-button" in suggestion.diff


def test_generates_api_contract_suggestion():
    case = NormalizedTestCase(
        name="test_create_user",
        classname="test_api",
        status="failed",
        message="TypeError: unexpected keyword argument 'name'",
    )
    source = 'def test_create_user():\n    client.create_user(name="Ana")\n'
    diff = """@@ -1 +1 @@
-def create_user(name):
+def create_user(username):
"""

    suggestion = (
        AutoHealingAnalyzer()
        .analyze(result_for(case), [context(case, source, diff)])
        .suggestions[0]
    )

    assert suggestion.problem_type == "api_contract_changed"
    assert suggestion.old_text == "name"
    assert suggestion.new_text == "username"


def test_does_not_suggest_fix_for_possible_application_defect():
    case = NormalizedTestCase(
        name="test_total",
        classname="test_calculator",
        status="failed",
        message="assert 3 == 4",
    )
    source = "def test_total():\n    assert calculate(2) == 4\n"
    diff = """@@ -1 +1 @@
-return value
+return value + tax
"""

    suggestion = (
        AutoHealingAnalyzer()
        .analyze(result_for(case), [context(case, source, diff)])
        .suggestions[0]
    )

    assert suggestion.classification == "application_defect"
    assert suggestion.state == "inconclusive"
    assert suggestion.file_path == ""
    assert suggestion.diff == ""


def test_missing_context_is_inconclusive():
    case = NormalizedTestCase(name="test_unknown", status="failed", message="failed")

    suggestion = AutoHealingAnalyzer().analyze(result_for(case)).suggestions[0]

    assert suggestion.classification == "insufficient_evidence"
    assert suggestion.state == "inconclusive"


def test_passed_cases_are_ignored_and_normalized_result_is_preserved():
    failed = NormalizedTestCase(name="test_bad", status="failed", message="selector not found")
    passed = NormalizedTestCase(name="test_ok", status="passed")
    result = result_for(failed).model_copy(update={"cases": [passed, failed]})

    report = AutoHealingAnalyzer().analyze(result)

    assert len(report.suggestions) == 1
    assert report.suggestions[0].test_name == "test_bad"
    assert report.run_id == result.id
