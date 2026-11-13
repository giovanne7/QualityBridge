"""Métricas determinísticas para identificar testes com resultados instáveis."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from qualitybridge.models import RunResult


@dataclass(frozen=True)
class FlakyMetric:
    name: str
    classname: str
    runner: str
    suite: str
    executions: int
    failures: int
    failure_rate: float
    alternations: int
    alternation_rate: float
    classification: str
    quarantine_recommended: bool


def analyze_runs(
    runs: list[RunResult], *, min_runs: int = 3, failure_threshold: float = 0.1
) -> list[FlakyMetric]:
    """Calcula métricas por teste, separando runner, suíte e identidade do caso."""
    if min_runs < 2:
        raise ValueError("min_runs deve ser pelo menos 2.")
    if not 0 <= failure_threshold <= 1:
        raise ValueError("failure_threshold deve estar entre 0 e 1.")

    grouped: dict[tuple[str, str, str, str], list[tuple[datetime, str]]] = defaultdict(list)
    for run in sorted(runs, key=lambda item: (item.started_at, item.id)):
        for case in run.cases:
            if case.status not in ("passed", "failed", "error"):
                continue
            key = (run.runner, run.suite, case.classname, case.name)
            grouped[key].append((run.started_at, "failed" if case.status != "passed" else "passed"))

    metrics = []
    for (runner, suite, classname, name), outcomes in grouped.items():
        outcomes.sort(key=lambda item: item[0])
        states = [state for _, state in outcomes]
        count = len(states)
        failures = states.count("failed")
        alternations = sum(left != right for left, right in pairwise(states))
        failure_rate = failures / count
        alternation_rate = alternations / max(1, count - 1)
        enough_data = count >= min_runs
        suspect = (
            enough_data
            and 0 < failure_rate < 1
            and alternations > 0
            and (failure_rate >= failure_threshold)
        )
        if not enough_data:
            classification = "dados_insuficientes"
        elif suspect:
            classification = "suspeito_intermitente"
        elif failures:
            classification = "falha_recorrente"
        else:
            classification = "estavel"
        metrics.append(
            FlakyMetric(
                name=name,
                classname=classname,
                runner=runner,
                suite=suite,
                executions=count,
                failures=failures,
                failure_rate=failure_rate,
                alternations=alternations,
                alternation_rate=alternation_rate,
                classification=classification,
                quarantine_recommended=suspect,
            )
        )
    return sorted(
        metrics,
        key=lambda metric: (
            -metric.failure_rate,
            -metric.alternation_rate,
            metric.name,
        ),
    )
