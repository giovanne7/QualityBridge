"""Sugestões heurísticas explicáveis para falhas observadas no histórico."""

import re
from collections import defaultdict
from dataclasses import asdict, dataclass

from qualitybridge.models import RunResult


@dataclass(frozen=True)
class CorrectionSuggestion:
    test: str
    runner: str
    suite: str
    category: str
    suggestion: str
    occurrences: int
    confidence: str
    evidence: list[str]
    execution_conditions: list[dict[str, object]]


RULES = (
    (
        "timeout",
        ("timeout", "timed out", "time limit", "tempo limite"),
        (
            "Investigue lentidão ou espera não sincronizada; prefira aguardar a condição do sistema. "
            "Aumente o timeout somente se a duração esperada justificar."
        ),
    ),
    (
        "rede",
        (
            "connection refused",
            "connection reset",
            "network",
            "econnreset",
            r"regex:\benotfound\b",
        ),
        (
            "Verifique disponibilidade do serviço e dependências externas; use dependência local "
            "controlada ou retry limitado apenas para erros transitórios identificados."
        ),
    ),
    (
        "concorrência ou recurso compartilhado",
        ("deadlock", "lock", "address already in use", "resource busy", "concurrent"),
        (
            "Isole arquivos, portas e dados compartilhados por execução; confira limpeza de fixtures "
            "e colisões entre testes paralelos."
        ),
    ),
    (
        "preparação ou ambiente",
        (
            "module not found",
            "modulenotfounderror",
            "cannot find module",
            "no such file",
            "not installed",
            "setup",
        ),
        (
            "Revise dependências, fixtures e pré-condições do ambiente; torne a preparação explícita "
            "e reproduzível."
        ),
    ),
    (
        "asserção ou comportamento",
        ("assert", "assertionerror", "expect("),
        (
            "Compare valor esperado e observado e verifique se o teste depende de horário, ordem, "
            "aleatoriedade ou estado compartilhado."
        ),
    ),
    (
        "dependência de ordem ou estado",
        (
            "order-dependent",
            "order dependent",
            "depends on order",
            "previous test",
            "shared state",
            "random seed",
        ),
        (
            "Torne o teste independente dos anteriores: inicialize e limpe o estado por teste, "
            "evite ordem fixa e use sementes explícitas para dados aleatórios."
        ),
    ),
)
ADVICE = {category: recommendation for category, _, recommendation in RULES}
ADVICE["causa não classificada"] = (
    "Inspecione o stack trace e compare execuções aprovadas e reprovadas; "
    "a evidência disponível não permite inferir uma correção específica."
)


def suggest_corrections(runs: list[RunResult]) -> list[CorrectionSuggestion]:
    grouped: dict[tuple[str, str, str, str, str], list[str]] = defaultdict(list)
    for run in runs:
        for case in run.cases:
            if case.status not in ("failed", "error"):
                continue
            evidence = "\n".join((case.message, case.stack_trace, *run.diagnostics)).lower()
            category = "causa não classificada"
            for candidate, keywords, _recommendation in RULES:
                assertion_word = candidate == "asserção ou comportamento" and re.search(
                    r"\b(?:expected|actual)\b", evidence
                )
                keyword_match = any(
                    bool(re.search(word[6:], evidence))
                    if word.startswith("regex:")
                    else word in evidence
                    for word in keywords
                )
                if assertion_word or keyword_match:
                    category = candidate
                    break
            key = (run.runner, run.suite, case.classname, case.name, category)
            excerpt = "\n".join(
                part for part in (case.message, case.stack_trace, *run.diagnostics) if part.strip()
            ).strip()
            if excerpt:
                grouped[key].append(excerpt[:240])

    suggestions = []
    for (runner, suite, classname, name, category), evidence in grouped.items():
        conditions = []
        for run in runs:
            if (run.runner, run.suite) != (runner, suite) or run.context is None:
                continue
            for case in run.cases:
                if (case.classname, case.name) == (classname, name):
                    conditions.append(
                        {
                            "outcome": case.status,
                            "context": run.context.model_dump(mode="json"),
                        }
                    )
        count = len(evidence)
        suggestions.append(
            CorrectionSuggestion(
                test=f"{classname}::{name}" if classname else name,
                runner=runner,
                suite=suite,
                category=category,
                suggestion=ADVICE[category],
                occurrences=count,
                confidence=(
                    "média" if count >= 3 and category != "causa não classificada" else "baixa"
                ),
                evidence=list(dict.fromkeys(evidence))[:3],
                execution_conditions=list({repr(item): item for item in conditions}.values())[:6],
            )
        )
    suggestions.sort(key=lambda item: (-item.occurrences, item.test))
    return suggestions


def suggestions_as_dicts(runs: list[RunResult]) -> list[dict[str, object]]:
    return [asdict(item) for item in suggest_corrections(runs)]
