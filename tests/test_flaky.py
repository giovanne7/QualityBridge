from datetime import UTC, datetime, timedelta

import pytest

from qualitybridge.flaky import analyze_runs
from qualitybridge.models import ExecutionContext, RunResult
from qualitybridge.models import TestCase as Case


def make_run(index, status, *, runner="pytest", suite="full", context=None):
    return RunResult(
        id=f"run-{index}",
        project="Demo",
        runner=runner,
        suite=suite,
        started_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
        duration_seconds=1,
        status="failed" if status == "failed" else "passed",
        exit_code=1 if status == "failed" else 0,
        command=[],
        context=context,
        cases=[Case(name="test_demo", classname="tests.test_demo", status=status)],
    )


def test_detects_alternation_and_reports_execution_context():
    linux = ExecutionContext(platform="Linux", timeout_seconds=30)
    windows = ExecutionContext(platform="Windows", timeout_seconds=30)
    runs = [
        make_run(0, "failed", context=linux),
        make_run(1, "passed", context=windows),
        make_run(2, "failed", context=linux),
    ]

    metrics = analyze_runs(runs)

    assert len(metrics) == 1
    assert metrics[0].classification == "suspeito_intermitente"
    assert metrics[0].failure_rate == pytest.approx(2 / 3)
    assert metrics[0].alternations == 2
    assert metrics[0].quarantine_recommended is True


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["passed", "passed", "passed"], "estavel"),
        (["failed", "failed", "failed"], "falha_recorrente"),
        (["failed", "passed"], "dados_insuficientes"),
    ],
)
def test_does_not_label_stable_or_recurring_failures_as_flaky(states, expected):
    metrics = analyze_runs([make_run(i, state) for i, state in enumerate(states)])

    assert metrics[0].classification == expected
    assert metrics[0].quarantine_recommended is False


def test_separates_runner_and_suite_and_rejects_invalid_threshold():
    runs = [
        make_run(0, "failed", runner="pytest", suite="full"),
        make_run(1, "passed", runner="jest", suite="full"),
        make_run(2, "passed", runner="pytest", suite="quick"),
    ]

    assert len(analyze_runs(runs)) == 3
    with pytest.raises(ValueError, match="failure_threshold"):
        analyze_runs(runs, failure_threshold=1.1)
