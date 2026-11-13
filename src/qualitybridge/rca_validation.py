"""Validação do RCA com cenários de causa conhecida.

Cada cenário descreve uma falha normalizada, alterações Git simuladas, o histórico
anterior do teste e a resposta esperada (categoria principal e commit culpado).
As métricas medem acerto, ausência de falsos vínculos, fundamentação e tempo.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qualitybridge.llm import LLMClient
from qualitybridge.models import RunResult, TestCase
from qualitybridge.rca import GitChange, RCADiagnosis, RootCauseAnalyzer, StaticChanges

CURRENT_RUN_AT = datetime(2026, 1, 2, tzinfo=UTC)


@dataclass(frozen=True)
class ScenarioResult:
    id: str
    expected_category: str
    obtained_category: str
    expected_sha: str | None
    obtained_sha: str | None
    category_ok: bool
    sha_ok: bool
    grounded: bool
    engine: str
    milliseconds: float


def load_scenarios(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run(run_id: str, runner: str, case: TestCase, started_at: datetime, status: str) -> RunResult:
    return RunResult(
        id=run_id,
        project="cenarios-rca",
        runner=runner,
        suite="full",
        started_at=started_at,
        duration_seconds=0.1,
        status=status,
        exit_code=0 if status == "passed" else 1,
        command=[runner],
        cases=[case],
    )


def build_runs(scenario: dict) -> tuple[RunResult, list[RunResult]]:
    """Cria a execução atual e as anteriores (a mais recente fica 1 dia antes)."""

    case = TestCase.model_validate(scenario["case"])
    runner = scenario.get("runner", "pytest")
    current = _run(f"{scenario['id']}-atual", runner, case, CURRENT_RUN_AT, "failed")
    previous = scenario.get("previous", [])
    history = []
    for index, status in enumerate(previous):
        moment = CURRENT_RUN_AT - timedelta(days=len(previous) - index)
        old_case = case.model_copy(update={"status": status})
        history.append(_run(f"{scenario['id']}-{index}", runner, old_case, moment, status))
    return current, [*history, current]


def _grounded(diagnosis: RCADiagnosis) -> bool:
    ids = {item.id for item in diagnosis.evidence}
    return all(h.evidence_ids and set(h.evidence_ids) <= ids for h in diagnosis.hypotheses)


def evaluate(scenarios: list[dict], llm: LLMClient | None = None) -> list[ScenarioResult]:
    results = []
    for scenario in scenarios:
        current, history = build_runs(scenario)
        changes = [GitChange.model_validate(item) for item in scenario.get("changes", [])]
        analyzer = RootCauseAnalyzer(changes=StaticChanges(changes), llm=llm, history=history)
        start = time.perf_counter()
        diagnosis = analyzer.analyze(current).diagnoses[0]
        elapsed = (time.perf_counter() - start) * 1000
        expected = scenario["expected"]
        obtained_sha = diagnosis.related_changes[0].sha if diagnosis.related_changes else None
        obtained_category = diagnosis.primary.category if diagnosis.primary else ""
        results.append(
            ScenarioResult(
                id=scenario["id"],
                expected_category=expected["category"],
                obtained_category=obtained_category,
                expected_sha=expected.get("sha"),
                obtained_sha=obtained_sha,
                category_ok=obtained_category == expected["category"],
                sha_ok=obtained_sha == expected.get("sha"),
                grounded=_grounded(diagnosis),
                engine=diagnosis.engine,
                milliseconds=round(elapsed, 2),
            )
        )
    return results


def summary(results: list[ScenarioResult]) -> dict[str, object]:
    total = len(results) or 1
    return {
        "cenarios": len(results),
        "acerto_categoria": round(sum(r.category_ok for r in results) / total, 3),
        "acerto_commit": round(sum(r.sha_ok for r in results) / total, 3),
        "hipoteses_fundamentadas": round(sum(r.grounded for r in results) / total, 3),
        "tempo_medio_ms": round(sum(r.milliseconds for r in results) / total, 2),
        "resultados": [asdict(r) for r in results],
    }
