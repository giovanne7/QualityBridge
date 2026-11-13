import sqlite3
from datetime import UTC, datetime

from qualitybridge.history import HistoryStore
from qualitybridge.models import ExecutionContext, RunResult


def result(context=None):
    return RunResult(
        id="run-1",
        project="Demo",
        runner="pytest",
        suite="full",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        duration_seconds=1,
        status="passed",
        exit_code=0,
        command=[],
        context=context,
    )


def test_round_trips_execution_context_and_reads_legacy_payload(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    store.save(
        result(
            ExecutionContext(
                platform="Windows",
                python_version="3.12.1",
                configured_env_keys=["API_TOKEN"],
                timeout_seconds=45,
            )
        )
    )

    restored = store.recent("Demo")[0]

    assert restored.context is not None
    assert restored.context.python_version == "3.12.1"
    assert restored.context.configured_env_keys == ["API_TOKEN"]
    assert "secret" not in restored.model_dump_json()
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?)",
            (
                "legacy",
                "Demo",
                "2025-12-31T00:00:00+00:00",
                result().model_copy(update={"id": "legacy"}).model_dump_json(exclude={"context"}),
            ),
        )
    legacy = next(run for run in store.recent("Demo", 10) if run.id == "legacy")
    assert legacy.context is None


def test_quarantine_lifecycle_is_reversible_and_audited(tmp_path):
    store = HistoryStore(tmp_path / "nested" / "history.sqlite3")
    identity = ("Demo", "pytest", "full", "tests.test_demo", "test_flaky")
    store.quarantine(*identity, "quarantine", "alternou 3 vezes", "dev@example.test")

    active = store.quarantined("Demo")
    assert len(active) == 1
    assert active[0]["status"] == "quarantined"
    assert store.quarantine_reasons("Demo", "pytest", "full") == {
        ("tests.test_demo", "test_flaky"): "alternou 3 vezes"
    }

    store.quarantine(*identity, "release", "corrigido", "dev@example.test")

    assert store.quarantined("Demo") == []
    assert store.quarantine_reasons("Demo", "pytest", "full") == {}
    audit = store.quarantine_audit("Demo")
    assert [event["action"] for event in audit] == ["release", "quarantine"]
    assert audit[0]["actor"] == "dev@example.test"
