from datetime import UTC, datetime, timedelta

import pytest

from qualitybridge.models import ExecutionContext, RunResult
from qualitybridge.models import TestCase as Case
from qualitybridge.suggestions import suggest_corrections


def failed_run(index, message, stack="", context=None):
    return RunResult(
        id=f"run-{index}",
        project="Demo",
        runner="pytest",
        suite="full",
        started_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
        duration_seconds=1,
        status="failed",
        exit_code=1,
        command=[],
        context=context,
        cases=[
            Case(
                name="test_fetch",
                classname="tests.test_api",
                status="failed",
                message=message,
                stack_trace=stack,
            )
        ],
    )


def test_suggestion_includes_evidence_confidence_and_conditions():
    context = ExecutionContext(platform="Linux", timeout_seconds=60)
    runs = [
        failed_run(i, "request failed", "ConnectionResetError: ECONNRESET", context)
        for i in range(3)
    ]
    passed = failed_run(4, "", "", ExecutionContext(platform="Windows", timeout_seconds=60))
    passed.cases[0] = passed.cases[0].model_copy(update={"status": "passed"})
    passed.status = "passed"
    runs.append(passed)

    [suggestion] = suggest_corrections(runs)

    assert suggestion.category == "rede"
    assert suggestion.occurrences == 3
    assert suggestion.confidence == "média"
    assert "ECONNRESET" in suggestion.evidence[0]
    assert [condition["outcome"] for condition in suggestion.execution_conditions] == [
        "failed",
        "passed",
    ]
    assert suggestion.execution_conditions[0]["context"] == context.model_dump(mode="json")


def test_unknown_failure_is_reported_as_low_confidence_and_ignores_passes():
    run = failed_run(0, "unexpected output")
    run.cases.append(Case(name="ok", status="passed"))

    [suggestion] = suggest_corrections([run])

    assert suggestion.category == "causa não classificada"
    assert suggestion.confidence == "baixa"
    assert suggestion.execution_conditions == []


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("timeout exceeded", "timeout"),
        ("Connection refused", "rede"),
        ("address already in use", "concorrência ou recurso compartilhado"),
        ("ModuleNotFoundError", "preparação ou ambiente"),
        ("AssertionError: expected 2", "asserção ou comportamento"),
        ("order-dependent shared state", "dependência de ordem ou estado"),
    ],
)
def test_classifies_common_failure_patterns(message, category):
    [suggestion] = suggest_corrections([failed_run(0, message)])

    assert suggestion.category == category
