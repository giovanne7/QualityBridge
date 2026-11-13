"""Análise determinística e conservadora de possíveis testes desatualizados.

Este módulo não altera arquivos e não executa comandos. A validação fica em
``healing_validation`` para manter análise e execução separadas.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from typing import Literal

from pydantic import Field

from qualitybridge.models import RunResult, StrictModel, TestCase

ProblemType = Literal[
    "selector_changed",
    "api_contract_changed",
    "assertion_contract_mismatch",
    "application_defect",
    "unknown",
]
Classification = Literal["outdated_test", "application_defect", "insufficient_evidence"]
SuggestionState = Literal["proposed", "validated", "rejected", "inconclusive"]


class FailureContext(StrictModel):
    """Contexto opcional fornecido pelo executor, RCA ou orquestrador."""

    case: TestCase
    test_file: str = ""
    test_source: str = ""
    related_source: str = ""
    recent_diff: str = ""
    rca_evidence: list[str] = Field(default_factory=list)


class HealingSuggestion(StrictModel):
    """Proposta estruturada de alteração, sem efeitos colaterais."""

    test_name: str
    problem_type: ProblemType
    classification: Classification
    explanation: str
    evidence: list[str] = Field(default_factory=list)
    file_path: str = ""
    old_text: str = ""
    new_text: str = ""
    diff: str = ""
    state: SuggestionState = "proposed"
    confidence: float = Field(default=0, ge=0, le=1)
    validation_message: str = ""
    validation_exit_code: int | None = None


class AutoHealingReport(StrictModel):
    """Saída serializável para futura integração com o orquestrador."""

    run_id: str
    suggestions: list[HealingSuggestion] = Field(default_factory=list)


_LITERAL = re.compile(r"(['\"])(.*?)\1")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{2,}")
_SELECTOR_MARKERS = (
    "locator",
    "selector",
    "getby",
    "queryselector",
    "find_element",
    "data-testid",
    "strict mode",
    "no element",
    "element not found",
)
_API_MARKERS = (
    r"unexpected keyword",
    r"missing .* argument",
    r"required argument",
    r"parameter",
    r"argument",
    r"status code",
    r"contract",
)


def _failure_text(case: TestCase) -> str:
    return "\n".join(part for part in (case.message, case.stack_trace) if part).lower()


def _diff_lines(diff: str) -> list[tuple[str, str]]:
    """Retorna pares simples de linhas removida/adicionada em um mesmo hunk."""

    pairs: list[tuple[str, str]] = []
    removed: list[str] = []
    added: list[str] = []

    def flush() -> None:
        count = min(len(removed), len(added))
        pairs.extend(zip(removed[:count], added[:count]))
        removed.clear()
        added.clear()

    for line in diff.splitlines():
        if line.startswith("@@"):
            flush()
        elif line.startswith(("---", "+++")):
            continue
        elif line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
        else:
            flush()
    flush()
    return pairs


def _changed_token(old_line: str, new_line: str, source: str) -> tuple[str, str] | None:
    """Encontra uma troca explícita de token aplicável ao código do teste."""

    old_literals = [value for _, value in _LITERAL.findall(old_line)]
    new_literals = [value for _, value in _LITERAL.findall(new_line)]
    for old, new in zip(old_literals, new_literals):
        if old != new and len(old) >= 2 and old in source:
            return old, new

    old_tokens = [token for token in _TOKEN.findall(old_line)]
    new_tokens = [token for token in _TOKEN.findall(new_line)]
    common = set(old_tokens) & set(new_tokens)
    for old in old_tokens:
        if old not in common and old in source:
            for new in new_tokens:
                if new not in common and new != old:
                    return old, new
    return None


def _file_diff(source: str, updated: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            source.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _inconclusive(case: TestCase, reason: str) -> HealingSuggestion:
    return HealingSuggestion(
        test_name=case.name,
        problem_type="unknown",
        classification="insufficient_evidence",
        explanation=reason,
        evidence=[case.message] if case.message else [],
        state="inconclusive",
    )


class AutoHealingAnalyzer:
    """Gera sugestões apenas quando existe uma alteração textual verificável."""

    def analyze(
        self, result: RunResult, contexts: Iterable[FailureContext] = ()
    ) -> AutoHealingReport:
        by_case = {(context.case.name, context.case.classname): context for context in contexts}
        suggestions = []

        for case in result.cases:
            if case.status not in ("failed", "error"):
                continue
            context = by_case.get((case.name, case.classname))
            if context is None:
                suggestions.append(
                    _inconclusive(
                        case, "Não há contexto de código ou alteração para analisar a falha."
                    )
                )
                continue
            suggestions.append(self._analyze_context(context))

        return AutoHealingReport(run_id=result.id, suggestions=suggestions)

    def _analyze_context(self, context: FailureContext) -> HealingSuggestion:
        case = context.case
        failure = _failure_text(case)
        if not context.test_source or not context.test_file or not context.recent_diff:
            if "assert" in failure or "expected" in failure:
                return HealingSuggestion(
                    test_name=case.name,
                    problem_type="application_defect",
                    classification="application_defect",
                    explanation=(
                        "A falha parece uma incompatibilidade da aplicação, mas não há "
                        "evidências suficientes para propor uma alteração no teste."
                    ),
                    evidence=[case.message, *context.rca_evidence],
                    state="inconclusive",
                )
            return _inconclusive(
                case,
                "O contexto não contém teste, arquivo ou diff recente suficientes para uma sugestão.",
            )

        selector_failure = any(marker in failure for marker in _SELECTOR_MARKERS)
        api_failure = any(re.search(marker, failure) for marker in _API_MARKERS)
        assertion_failure = "assert" in failure or "expected" in failure

        for old_line, new_line in _diff_lines(context.recent_diff):
            if difflib.SequenceMatcher(None, old_line, new_line).ratio() < 0.35:
                continue
            changed = _changed_token(old_line, new_line, context.test_source)
            if changed is None:
                continue
            old_text, new_text = changed
            updated_source = context.test_source.replace(old_text, new_text, 1)
            if updated_source == context.test_source:
                continue

            if selector_failure:
                problem_type: ProblemType = "selector_changed"
                explanation = (
                    "A mensagem indica seletor inexistente e o diff recente mostra uma "
                    "troca explícita que também aparece no código do teste."
                )
            elif api_failure:
                problem_type = "api_contract_changed"
                explanation = (
                    "A mensagem indica mudança de parâmetro/contrato e o diff fornece "
                    "um token novo que substitui exatamente o token usado pelo teste."
                )
            elif assertion_failure:
                problem_type = "assertion_contract_mismatch"
                explanation = (
                    "A asserção falhou e o diff recente fornece uma alteração explícita "
                    "que pode atualizar o valor esperado do teste."
                )
            else:
                continue

            return HealingSuggestion(
                test_name=case.name,
                problem_type=problem_type,
                classification="outdated_test",
                explanation=explanation,
                evidence=[
                    f"Falha normalizada: {case.message or 'sem mensagem'}",
                    f"Diff removido: {old_line}",
                    f"Diff adicionado: {new_line}",
                    f"Token encontrado no teste: {old_text} -> {new_text}",
                    *context.rca_evidence,
                ],
                file_path=context.test_file,
                old_text=old_text,
                new_text=new_text,
                diff=_file_diff(context.test_source, updated_source, context.test_file),
                confidence=0.9,
            )

        if assertion_failure:
            return HealingSuggestion(
                test_name=case.name,
                problem_type="application_defect",
                classification="application_defect",
                explanation=(
                    "A falha de asserção não possui uma alteração correlacionada aplicável "
                    "ao código do teste; nenhuma correção automática será proposta."
                ),
                evidence=[case.message, *context.rca_evidence],
                state="inconclusive",
            )
        return _inconclusive(
            case, "O diff não contém uma alteração aplicável com evidência suficiente."
        )


def apply_validation_state(
    suggestion: HealingSuggestion,
    state: SuggestionState,
    message: str,
    exit_code: int | None = None,
) -> HealingSuggestion:
    """Atualiza o estado sem modificar o arquivo ou a sugestão original em memória."""

    return suggestion.model_copy(
        update={
            "state": state,
            "validation_message": message,
            "validation_exit_code": exit_code,
        }
    )
