"""Análise de causa raiz (RCA) das falhas de teste.

Fluxo por caso reprovado:

1. interpreta mensagem e stack trace (pytest, traceback Python, Jest e Playwright);
2. relaciona arquivos, linhas e símbolos do stack trace com alterações recentes do Git;
3. consulta o histórico de execuções (regressão recente ou intermitência);
4. gera hipóteses determinísticas, cada uma com evidências identificadas (E1, E2...);
5. opcionalmente consulta um LLM com prompt estruturado e aceita apenas hipóteses
   que citem evidências existentes. Se o LLM falhar, o resultado heurístico é mantido.

O módulo não altera arquivos. ``RCADiagnosis.as_rca_evidence`` alimenta o campo
``FailureContext.rca_evidence`` do Auto-healing.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from qualitybridge.flaky import analyze_runs
from qualitybridge.llm import LLMClient, LLMError
from qualitybridge.models import RunResult, StrictModel, TestCase
from qualitybridge.rca_prompts import CATEGORIES, SYSTEM_PROMPT, build_user_prompt

EvidenceKind = Literal["mensagem", "excecao", "stack", "git", "historico"]
LinkKind = Literal["linha_alterada", "arquivo_do_stack", "simbolo_alterado", "arquivo_do_teste"]
Engine = Literal["heuristic", "llm", "heuristic_fallback"]

MAX_DIFF_CHARS = 20_000
MAX_EXCERPT_CHARS = 1_500
MAX_LLM_CONFIDENCE = 0.9
CLOCK_TOLERANCE = timedelta(seconds=60)


# --------------------------------------------------------------------------- modelos


class StackFrame(StrictModel):
    file: str
    line: int | None = None
    function: str = ""
    library: bool = False
    test_code: bool = False


class GitChange(StrictModel):
    """Um commit recente ou as alterações ainda não commitadas (``uncommitted``)."""

    sha: str
    subject: str = ""
    author: str = ""
    date: str = ""
    files: list[str] = Field(default_factory=list)
    diff: str = ""
    uncommitted: bool = False


class ChangeLink(StrictModel):
    sha: str
    subject: str
    file: str
    kind: LinkKind
    line: int | None = None
    symbol: str = ""
    excerpt: str = ""


class Evidence(StrictModel):
    id: str
    kind: EvidenceKind
    content: str


class Hypothesis(StrictModel):
    category: str
    cause: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    suggested_fix: list[str] = Field(default_factory=list)
    source: Literal["heuristic", "llm"] = "heuristic"


class RCADiagnosis(StrictModel):
    test_name: str
    classname: str = ""
    file_path: str = ""
    status: str
    exception_type: str = ""
    frames: list[StackFrame] = Field(default_factory=list)
    related_changes: list[ChangeLink] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    engine: Engine = "heuristic"
    llm_model: str = ""
    warnings: list[str] = Field(default_factory=list)

    @property
    def primary(self) -> Hypothesis | None:
        return self.hypotheses[0] if self.hypotheses else None

    def as_rca_evidence(self) -> list[str]:
        """Resumo textual para ``FailureContext.rca_evidence`` do Auto-healing."""

        if self.primary is None:
            return []
        by_id = {item.id: item for item in self.evidence}
        lines = [
            f"RCA {self.primary.category} ({self.primary.confidence:.0%}): {self.primary.cause}"
        ]
        lines += [
            f"RCA evidência {ref}: {by_id[ref].content[:200]}"
            for ref in self.primary.evidence_ids
            if ref in by_id
        ]
        return lines


class RCAReport(StrictModel):
    schema_version: int = 1
    run_id: str
    project: str
    runner: str
    suite: str
    generated_at: datetime
    git_available: bool = False
    changes_analyzed: int = 0
    diagnoses: list[RCADiagnosis] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def evidence_by_case(self) -> dict[tuple[str, str], list[str]]:
        return {(d.classname, d.test_name): d.as_rca_evidence() for d in self.diagnoses}


# --------------------------------------------------------------------------- stack trace

_PY_TRACEBACK = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>[\w<>.]+)')
_PYTEST_LOCATION = re.compile(
    r"^(?P<file>[^\s:][^:\n]*?\.py):(?P<line>\d+):"
    r"(?:\s+(?:in\s+(?P<func>[\w<>.]+)|(?P<exc>[\w.]+)))?\s*$"
)
_JS_FRAME = re.compile(
    r"^\s*at\s+(?:(?P<func>[^\s(][^(]*?)\s+\()?(?P<file>[^\s()]+?\.(?:js|jsx|ts|tsx|mjs|cjs))"
    r":(?P<line>\d+)(?::\d+)?\)?\s*$"
)
_PY_DEF = re.compile(r"^\s*(?:>\s*)?(?:async\s+)?def\s+(\w+)\(")
_E_LINE = re.compile(r"^E\s+(?P<exc>[A-Za-z_][\w.]*?(?:Error|Exception|Exit|Warning)):")
_LEADING_EXC = re.compile(r"^(?P<exc>[A-Za-z_][\w.]*?(?:Error|Exception))(?::|\s|$)")
_CALL = re.compile(r"\b([A-Za-z_]\w{2,})\(")
_IGNORED_SYMBOLS = {
    "assert", "expect", "len", "str", "int", "float", "bool", "dict", "list", "set",
    "tuple", "print", "isinstance", "repr", "type", "range", "toBe", "toEqual",
    "where", "test", "describe", "Error", "locator", "getByTestId", "getByRole",
}  # fmt: skip
_LIBRARY_MARKERS = (
    "site-packages", "dist-packages", "node_modules", "/lib/python", "<frozen",
    "_pytest/", "pluggy/", "internal/", "node:",
)  # fmt: skip


def _is_test_path(path: str) -> bool:
    name = PurePosixPath(path.replace("\\", "/")).name
    parts = path.replace("\\", "/").split("/")
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or ".spec." in name
        or ".test." in name
        or any(part in ("tests", "test", "__tests__", "e2e") for part in parts[:-1])
    )


def _frame(file: str, line: str | None, function: str = "") -> StackFrame:
    normalized = file.replace("\\", "/")
    normalized = normalized.removeprefix("file://")
    return StackFrame(
        file=normalized,
        line=int(line) if line else None,
        function=function or "",
        library=any(marker in normalized for marker in _LIBRARY_MARKERS),
        test_code=_is_test_path(normalized),
    )


def parse_failure(case: TestCase) -> tuple[str, list[StackFrame]]:
    """Retorna o tipo da exceção e os frames, do mais externo ao mais interno."""

    text = "\n".join(part for part in (case.message, case.stack_trace) if part)
    frames: list[StackFrame] = []
    exception = ""
    last_def = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if definition := _PY_DEF.match(line):
            last_def = definition.group(1)
        if match := _PY_TRACEBACK.search(line):
            frames.append(_frame(match["file"], match["line"], match["func"]))
        elif match := _PYTEST_LOCATION.match(line):
            frames.append(_frame(match["file"], match["line"], match["func"] or last_def))
            if match["exc"]:
                exception = match["exc"]
            last_def = ""
        elif match := _JS_FRAME.match(line):
            frames.append(_frame(match["file"], match["line"], (match["func"] or "").strip()))
        elif not exception and (match := _E_LINE.match(line)):
            exception = match["exc"]

    if not exception:
        for candidate in (case.message, case.stack_trace):
            if match := _LEADING_EXC.match(candidate.strip()):
                exception = match["exc"]
                break
    if not exception and re.search(r"\bassert|expect\(", text, re.IGNORECASE):
        exception = "AssertionError"

    unique: list[StackFrame] = []
    for frame in frames:
        if not any((f.file, f.line) == (frame.file, frame.line) for f in unique):
            unique.append(frame)
    return exception.split(".")[-1], unique


def _symbols(case: TestCase, frames: list[StackFrame]) -> list[str]:
    names = [f.function for f in frames if f.function and not f.library and not f.test_code]
    names += _CALL.findall(case.message)
    # Linhas marcadas com ">" no pytest mostram a instrução que falhou.
    for line in case.stack_trace.splitlines():
        if line.lstrip().startswith(">"):
            names += _CALL.findall(line)
    return [
        name
        for name in dict.fromkeys(names)
        if name not in _IGNORED_SYMBOLS and not name.startswith("test") and "<" not in name
    ]


# --------------------------------------------------------------------------- Git


class ChangeSource(Protocol):
    available: bool
    warnings: list[str]

    def recent_changes(self) -> list[GitChange]: ...


class StaticChanges:
    """Fonte fixa de alterações, usada em cenários conhecidos e testes."""

    def __init__(self, changes: Sequence[GitChange]):
        self._changes = list(changes)
        self.available = True
        self.warnings: list[str] = []

    def recent_changes(self) -> list[GitChange]:
        return list(self._changes)


class GitHistory:
    """Lê commits recentes com ``git`` sem shell. Caminhos ficam relativos à raiz do projeto."""

    def __init__(self, root: Path, max_commits: int = 10, timeout: int = 15):
        self.root = Path(root)
        self.max_commits = max_commits
        self.timeout = timeout
        self.available = False
        self.warnings: list[str] = []
        self._cache: list[GitChange] | None = None

    def _git(self, *args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.warnings.append(f"Git indisponível: {exc}")
            return None
        return completed.stdout if completed.returncode == 0 else None

    def recent_changes(self) -> list[GitChange]:
        if self._cache is not None:
            return self._cache
        self._cache = []
        if (self._git("rev-parse", "--is-inside-work-tree") or "").strip() != "true":
            self.warnings.append("O projeto não está em um repositório Git; correlação ignorada.")
            return self._cache
        self.available = True
        if self._git("rev-parse", "--verify", "HEAD") is None:
            self.warnings.append("Repositório Git sem commits.")
            return self._cache

        worktree = self._git("diff", "--no-color", "--relative", "-U3", "HEAD") or ""
        if worktree.strip():
            self._cache.append(
                GitChange(
                    sha="WORKTREE",
                    subject="alterações não commitadas",
                    files=sorted(_split_diff(worktree)),
                    diff=worktree[:MAX_DIFF_CHARS],
                    uncommitted=True,
                )
            )

        log = self._git(
            "log",
            f"-n{self.max_commits}",
            "--no-color",
            "--relative",
            "--name-only",
            "--format=%x1e%H%x1f%an%x1f%cI%x1f%s",
        )
        for record in (log or "").split("\x1e"):
            if not record.strip():
                continue
            header, _, names = record.partition("\n")
            sha, author, date, subject = (header.split("\x1f") + ["", "", "", ""])[:4]
            files = [name.strip() for name in names.splitlines() if name.strip()]
            if not files:
                continue
            diff = self._git("show", "--no-color", "--relative", "-U3", "--format=", sha) or ""
            self._cache.append(
                GitChange(
                    sha=sha,
                    subject=subject,
                    author=author,
                    date=date,
                    files=files,
                    diff=diff[:MAX_DIFF_CHARS],
                )
            )
        return self._cache


_DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def _split_diff(diff: str) -> dict[str, str]:
    headers = list(_DIFF_HEADER.finditer(diff))
    if not headers:
        return {}
    parts: dict[str, str] = {}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(diff)
        parts[header.group(2)] = diff[header.start() : end]
    return parts


def _same_file(changed: str, frame_file: str) -> bool:
    changed = changed.replace("\\", "/").lstrip("./")
    frame_file = frame_file.replace("\\", "/").lstrip("./")
    return (
        changed == frame_file
        or frame_file.endswith("/" + changed)
        or changed.endswith("/" + frame_file)
    )


def _line_changed(file_diff: str, line: int | None, margin: int = 2) -> bool:
    if line is None:
        return False
    for hunk in _HUNK.finditer(file_diff):
        start = int(hunk.group(1))
        length = int(hunk.group(2) or 1)
        if start - margin <= line <= start + max(length, 1) + margin:
            return True
    return False


def _hunks(file_diff: str) -> list[str]:
    """Trechos alterados, incluindo cabeçalho e linhas de contexto de cada hunk."""

    starts = [match.start() for match in _HUNK.finditer(file_diff)]
    return [
        file_diff[start : starts[index + 1] if index + 1 < len(starts) else len(file_diff)]
        for index, start in enumerate(starts)
    ]


def _excerpt(file_diff: str) -> str:
    start = file_diff.find("@@")
    body = file_diff[start:] if start >= 0 else file_diff
    return body[:MAX_EXCERPT_CHARS]


def correlate_changes(
    changes: Sequence[GitChange],
    frames: list[StackFrame],
    symbols: list[str],
    test_file: str = "",
) -> list[ChangeLink]:
    """Relaciona alterações com o stack trace. A ordem reflete a força da evidência."""

    code_frames = [f for f in frames if not f.library and not f.test_code]
    test_files = {f.file for f in frames if f.test_code and not f.library}
    if test_file:
        test_files.add(test_file.replace("\\", "/"))
    links: list[ChangeLink] = []
    for change in changes:
        per_file = _split_diff(change.diff)
        for changed in change.files:
            file_diff = per_file.get(changed, "")
            is_failing_test = any(_same_file(changed, name) for name in test_files)
            if is_failing_test or _is_test_path(changed):
                if is_failing_test:
                    links.append(
                        ChangeLink(
                            sha=change.sha,
                            subject=change.subject,
                            file=changed,
                            kind="arquivo_do_teste",
                            excerpt=_excerpt(file_diff),
                        )
                    )
                continue
            frame = next((f for f in code_frames if _same_file(changed, f.file)), None)
            if frame is not None:
                changed_line = _line_changed(file_diff, frame.line)
                links.append(
                    ChangeLink(
                        sha=change.sha,
                        subject=change.subject,
                        file=changed,
                        kind="linha_alterada" if changed_line else "arquivo_do_stack",
                        line=frame.line,
                        symbol=frame.function,
                        excerpt=_excerpt(file_diff),
                    )
                )
                continue
            hunks = _hunks(file_diff)
            symbol = next(
                (
                    name
                    for name in symbols
                    for hunk in hunks
                    if re.search(rf"\b{re.escape(name)}\b", hunk)
                ),
                None,
            )
            if symbol:
                links.append(
                    ChangeLink(
                        sha=change.sha,
                        subject=change.subject,
                        file=changed,
                        kind="simbolo_alterado",
                        symbol=symbol,
                        excerpt=_excerpt(file_diff),
                    )
                )
    strength = {"linha_alterada": 0, "arquivo_do_stack": 1, "simbolo_alterado": 2}
    return sorted(links, key=lambda link: strength.get(link.kind, 3))


# --------------------------------------------------------------------------- heurística

_RULES: tuple[tuple[str, frozenset[str], tuple[str, ...]], ...] = (
    (
        "seletor_interface",
        frozenset(),
        ("waiting for locator", "locator(", "selector", "getby", "queryselector",
         "find_element", "data-testid", "strict mode violation", "element not found"),
    ),
    (
        "tempo_sincronizacao",
        frozenset({"TimeoutError", "TimeoutExpired"}),
        ("timed out", "timeout", "time limit", "tempo limite"),
    ),
    (
        "dependencia_externa",
        frozenset({"ConnectionError", "ConnectionRefusedError", "ConnectionResetError"}),
        ("connection refused", "econnrefused", "econnreset", "enotfound",
         "name or service not known", "network error"),
    ),
    (
        "ambiente_configuracao",
        frozenset({"ImportError", "ModuleNotFoundError", "FileNotFoundError", "PermissionError"}),
        ("cannot find module", "no such file", "not installed", "command not found",
         "fixture '"),
    ),
    (
        "contrato_api",
        frozenset({"TypeError", "AttributeError", "NameError", "ReferenceError"}),
        ("unexpected keyword", "required positional argument", "is not a function",
         "has no attribute", "is not defined", "positional argument"),
    ),
    (
        "dados_invalidos",
        frozenset({"ZeroDivisionError", "IndexError", "KeyError", "ValueError",
                   "OverflowError", "RangeError"}),
        ("division by zero", "out of range", "invalid literal"),
    ),
    (
        "assercao_divergente",
        frozenset({"AssertionError", "AssertError"}),
        ("assert", "expect(", "expected", "received"),
    ),
)  # fmt: skip

_FIXES: dict[str, list[str]] = {
    "seletor_interface": [
        "Comparar o seletor usado no teste com o identificador atual da interface.",
        "Preferir seletores estáveis (data-testid, papel acessível) a textos e classes CSS.",
    ],
    "tempo_sincronizacao": [
        "Aguardar a condição do sistema em vez de usar esperas fixas.",
        "Aumentar o timeout somente se a duração esperada justificar.",
    ],
    "dependencia_externa": [
        "Verificar se o serviço externo está disponível no ambiente de teste.",
        "Substituir a dependência por um dublê local controlado.",
    ],
    "ambiente_configuracao": [
        "Conferir dependências instaladas, caminhos e fixtures exigidas pelo teste.",
        "Tornar a preparação do ambiente explícita e reproduzível.",
    ],
    "contrato_api": [
        "Comparar a chamada feita pelo teste com a assinatura/atributos atuais do código.",
        "Atualizar o chamador ou restaurar a compatibilidade da API.",
    ],
    "dados_invalidos": [
        "Validar a entrada que chega à função indicada no stack trace.",
        "Adicionar tratamento para o caso-limite revelado pelo teste.",
    ],
    "assercao_divergente": [
        "Comparar valor esperado e observado na asserção indicada.",
        "Confirmar a regra de negócio antes de alterar o teste ou o código.",
    ],
    "nao_classificada": [
        "Inspecionar o stack trace completo e o execution.log da execução.",
        "Reproduzir o teste isoladamente para coletar mais evidências.",
    ],
}


def _classify(exception: str, text: str) -> tuple[str, float]:
    lowered = text.lower()
    # Timeouts do Playwright aguardando um locator indicam seletor, não lentidão.
    if any(keyword in lowered for keyword in _RULES[0][2]):
        return "seletor_interface", 0.5
    for category, exceptions, _keywords in _RULES:
        if exception in exceptions:
            return category, 0.5
    for category, _exceptions, keywords in _RULES:
        if any(keyword in lowered for keyword in keywords):
            return category, 0.4
    return "nao_classificada", 0.2


class _EvidenceBook:
    def __init__(self) -> None:
        self.items: list[Evidence] = []

    def add(self, kind: EvidenceKind, content: str) -> str:
        for item in self.items:
            if (item.kind, item.content) == (kind, content):
                return item.id
        identifier = f"E{len(self.items) + 1}"
        self.items.append(Evidence(id=identifier, kind=kind, content=content))
        return identifier


class _HistorySignals(BaseModel):
    last_status: str | None = None
    last_passed_at: datetime | None = None
    flaky_class: str | None = None


def _history_signals(
    case: TestCase, result: RunResult, history: Sequence[RunResult]
) -> _HistorySignals:
    """Situação anterior do mesmo caso e classificação de intermitência."""

    same = [
        run
        for run in history
        if (run.runner, run.suite) == (result.runner, result.suite) and run.id != result.id
    ]
    previous = sorted(
        (run.started_at, item.status)
        for run in same
        if run.started_at <= result.started_at
        for item in run.cases
        if (item.classname, item.name) == (case.classname, case.name)
    )
    passed = [moment for moment, status in previous if status == "passed"]
    metrics = analyze_runs([*same, result], min_runs=3)
    return _HistorySignals(
        last_status=previous[-1][1] if previous else None,
        last_passed_at=passed[-1] if passed else None,
        flaky_class=next(
            (
                metric.classification
                for metric in metrics
                if (metric.classname, metric.name) == (case.classname, case.name)
            ),
            None,
        ),
    )


def _after(change: GitChange, moment: datetime | None) -> bool:
    """Mantém a alteração se ela pode ser posterior à última aprovação do teste."""

    if moment is None or change.uncommitted or not change.date:
        return True
    try:
        changed_at = datetime.fromisoformat(change.date)
    except ValueError:
        return True
    # Datas do Git têm precisão de segundos e relógios podem divergir um pouco.
    return changed_at > moment - CLOCK_TOLERANCE


# --------------------------------------------------------------------------- LLM


class _LLMHypothesis(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: str
    cause: str
    confidence: float
    evidence_ids: list[str]
    suggested_fix: list[str] = Field(default_factory=list)


class _LLMAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hypotheses: list[_LLMHypothesis]


def parse_llm_answer(raw: str, evidence_ids: set[str]) -> list[Hypothesis]:
    """Valida a resposta do modelo e descarta hipóteses não fundamentadas."""

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("A resposta do modelo não contém JSON.")
    try:
        answer = _LLMAnswer.model_validate(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMError(f"JSON do modelo fora do esquema: {exc}") from exc

    accepted: list[Hypothesis] = []
    for item in answer.hypotheses[:4]:
        cited = [ref for ref in dict.fromkeys(item.evidence_ids) if ref in evidence_ids]
        if not cited or not item.cause.strip():
            continue
        accepted.append(
            Hypothesis(
                category=item.category if item.category in CATEGORIES else "nao_classificada",
                cause=item.cause.strip()[:600],
                confidence=round(min(max(item.confidence, 0.0), MAX_LLM_CONFIDENCE), 2),
                evidence_ids=cited,
                suggested_fix=[fix.strip()[:300] for fix in item.suggested_fix if fix.strip()][:4],
                source="llm",
            )
        )
    if not accepted:
        raise LLMError("O modelo não apresentou hipóteses apoiadas em evidências conhecidas.")
    return accepted


# --------------------------------------------------------------------------- analisador


class RootCauseAnalyzer:
    """Gera diagnósticos fundamentados para os casos reprovados de uma execução."""

    def __init__(
        self,
        changes: ChangeSource | None = None,
        llm: LLMClient | None = None,
        history: Sequence[RunResult] = (),
        max_llm_cases: int = 10,
    ):
        self.changes = changes
        self.llm = llm
        self.history = list(history)
        self.max_llm_cases = max_llm_cases

    def analyze(self, result: RunResult) -> RCAReport:
        changes = self.changes.recent_changes() if self.changes else []
        diagnoses = []
        llm_budget = self.max_llm_cases
        skipped_llm = 0
        for case in result.cases:
            if case.status not in ("failed", "error"):
                continue
            use_llm = self.llm is not None and llm_budget > 0
            if use_llm:
                llm_budget -= 1
            elif self.llm is not None:
                skipped_llm += 1
            diagnoses.append(self.diagnose(case, result, changes, use_llm=use_llm))
        warnings = list(self.changes.warnings) if self.changes else []
        if skipped_llm:
            warnings.append(
                f"LLM limitado aos primeiros {self.max_llm_cases} casos; "
                f"{skipped_llm} analisado(s) só pela heurística."
            )
        return RCAReport(
            run_id=result.id,
            project=result.project,
            runner=result.runner,
            suite=result.suite,
            generated_at=datetime.now(UTC),
            git_available=bool(self.changes and self.changes.available),
            changes_analyzed=len(changes),
            diagnoses=diagnoses,
            warnings=list(dict.fromkeys(warnings)),
        )

    def diagnose(
        self,
        case: TestCase,
        result: RunResult,
        changes: Sequence[GitChange] = (),
        use_llm: bool = False,
    ) -> RCADiagnosis:
        exception, frames = parse_failure(case)
        symbols = _symbols(case, frames)
        signals = _history_signals(case, result, self.history)
        candidates = [change for change in changes if _after(change, signals.last_passed_at)]
        warnings = []
        if len(candidates) < len(changes):
            warnings.append(
                f"{len(changes) - len(candidates)} alteração(ões) anteriores à última aprovação "
                "do teste foram desconsideradas."
            )
        links = correlate_changes(candidates, frames, symbols, case.file_path)
        book = _EvidenceBook()
        hypotheses = self._heuristic(case, result, exception, frames, links, book, signals)
        diagnosis = RCADiagnosis(
            test_name=case.name,
            classname=case.classname,
            file_path=case.file_path,
            status=case.status,
            exception_type=exception,
            frames=frames[:12],
            related_changes=links[:8],
            evidence=book.items,
            hypotheses=hypotheses,
            warnings=warnings,
        )
        if use_llm and self.llm is not None:
            diagnosis = self._with_llm(diagnosis, case, result)
        return diagnosis

    # -- heurística determinística

    def _heuristic(
        self,
        case: TestCase,
        result: RunResult,
        exception: str,
        frames: list[StackFrame],
        links: list[ChangeLink],
        book: _EvidenceBook,
        signals: _HistorySignals,
    ) -> list[Hypothesis]:
        text = f"{case.message}\n{case.stack_trace}"
        category, confidence = _classify(exception, text)
        base_refs = []
        if case.message:
            base_refs.append(book.add("mensagem", case.message.strip()[:500]))
        if exception:
            base_refs.append(book.add("excecao", f"Tipo de erro: {exception}"))
        code_frames = [f for f in frames if not f.library]
        origin = next((f for f in reversed(code_frames) if not f.test_code), None)
        test_frame = next((f for f in code_frames if f.test_code), None)
        for frame in (origin, test_frame):
            if frame is not None:
                where = f"{frame.file}:{frame.line}" if frame.line else frame.file
                role = "código de teste" if frame.test_code else "código da aplicação"
                suffix = f" em {frame.function}()" if frame.function else ""
                base_refs.append(book.add("stack", f"Falha em {where}{suffix} ({role})"))

        location = f" em {origin.file}:{origin.line}" if origin and origin.line else ""
        hypotheses = [
            Hypothesis(
                category=category,
                cause=f"{CATEGORIES[category]}{location}.",
                confidence=confidence,
                evidence_ids=base_refs,
                suggested_fix=list(_FIXES.get(category, _FIXES["nao_classificada"])),
            )
        ]

        last_status, flaky_class = signals.last_status, signals.flaky_class
        passed_before = last_status == "passed"
        history_ref = None
        if last_status is not None:
            history_ref = book.add(
                "historico", f"Na execução anterior registrada o teste terminou como {last_status}."
            )
        if flaky_class == "suspeito_intermitente":
            flaky_ref = book.add(
                "historico", "O histórico classifica o teste como suspeito_intermitente."
            )
            hypotheses.append(
                Hypothesis(
                    category="intermitencia",
                    cause=(
                        "O mesmo teste alterna entre aprovação e falha sem alteração "
                        "correlacionada; a causa pode estar em tempo, ordem ou estado compartilhado."
                    ),
                    confidence=0.55 if links else 0.7,
                    evidence_ids=[flaky_ref, *base_refs[:1]],
                    suggested_fix=[
                        "Reexecutar com `qualitybridge flaky --rerun 5` para confirmar.",
                        "Isolar estado, horário e aleatoriedade usados pelo teste.",
                    ],
                )
            )

        code_links = [link for link in links if link.kind != "arquivo_do_teste"]
        test_links = [link for link in links if link.kind == "arquivo_do_teste"]
        if code_links:
            link = code_links[0]
            label = (
                "alterações não commitadas" if link.sha == "WORKTREE" else f"commit {link.sha[:8]}"
            )
            detail = {
                "linha_alterada": f"alterou a região da linha {link.line} de {link.file}",
                "arquivo_do_stack": f"alterou {link.file}, arquivo presente no stack trace",
                "simbolo_alterado": f"alterou {link.symbol} em {link.file}, citado na falha",
            }[link.kind]
            git_ref = book.add(
                "git", f"{label} ('{link.subject}') {detail}.\n{link.excerpt[:600]}".strip()
            )
            refs = [git_ref, *base_refs]
            if passed_before and history_ref:
                refs.append(history_ref)
            regression = min(
                0.55
                + (0.15 if link.kind == "linha_alterada" else 0)
                + (0.1 if passed_before else 0),
                0.9,
            )
            show = "git diff HEAD" if link.sha == "WORKTREE" else f"git show {link.sha[:8]}"
            hypotheses.append(
                Hypothesis(
                    category="regressao_codigo",
                    cause=f"A falha provavelmente foi introduzida por {label}, que {detail}.",
                    confidence=round(regression, 2),
                    evidence_ids=refs,
                    suggested_fix=[
                        f"Revisar `{show} -- {link.file}`.",
                        (
                            "Desfazer a alteração em uma cópia local e reexecutar o teste para "
                            "confirmar a causa."
                        ),
                        (
                            "Se a mudança foi intencional, tratar como teste desatualizado "
                            "(módulo Auto-healing)."
                        ),
                    ],
                )
            )
            test_updated_later = any(
                change.sha == link.sha for change in test_links
            )  # teste já acompanhou a mudança
            if (
                category in ("assercao_divergente", "contrato_api", "seletor_interface")
                and not test_updated_later
            ):
                hypotheses.append(
                    Hypothesis(
                        category="teste_desatualizado",
                        cause=(
                            f"Se a mudança de {label} em {link.file} foi intencional, o teste "
                            "ainda verifica o comportamento anterior."
                        ),
                        confidence=round(max(regression - 0.2, 0.3), 2),
                        evidence_ids=[git_ref, *base_refs[:1]],
                        suggested_fix=[
                            (
                                "Confirmar com o responsável pela alteração qual é o comportamento "
                                "esperado."
                            ),
                            "Gerar e validar a correção do teste com o módulo Auto-healing.",
                        ],
                    )
                )
        elif test_links:
            link = test_links[0]
            git_ref = book.add(
                "git", f"O próprio teste foi alterado em {link.sha[:8]} ('{link.subject}')."
            )
            hypotheses[0] = hypotheses[0].model_copy(
                update={
                    "confidence": min(hypotheses[0].confidence + 0.1, 0.9),
                    "evidence_ids": [*hypotheses[0].evidence_ids, git_ref],
                    "cause": hypotheses[0].cause
                    + " O arquivo do teste mudou recentemente; revisar a própria alteração.",
                }
            )
        elif passed_before and history_ref:
            hypotheses[0] = hypotheses[0].model_copy(
                update={
                    "evidence_ids": [*hypotheses[0].evidence_ids, history_ref],
                    "cause": hypotheses[0].cause
                    + " O teste passava antes e nenhum commit analisado foi relacionado;"
                    " considerar ambiente, dados ou dependências.",
                }
            )

        return sorted(hypotheses, key=lambda item: -item.confidence)

    # -- LLM opcional

    def _with_llm(self, diagnosis: RCADiagnosis, case: TestCase, result: RunResult) -> RCADiagnosis:
        evidence = list(diagnosis.evidence)
        book_ids = {item.id for item in evidence}
        if case.stack_trace and len(evidence) < 30:
            identifier = f"E{len(evidence) + 1}"
            evidence.append(Evidence(id=identifier, kind="stack", content=case.stack_trace[-3000:]))
            book_ids.add(identifier)
        for link in diagnosis.related_changes[:3]:
            identifier = f"E{len(evidence) + 1}"
            evidence.append(
                Evidence(
                    id=identifier,
                    kind="git",
                    content=f"{link.sha[:8]} '{link.subject}' {link.file}\n{link.excerpt}",
                )
            )
            book_ids.add(identifier)

        context = {
            "teste": {
                "nome": case.name,
                "classe": case.classname,
                "arquivo": case.file_path,
                "status": case.status,
                "runner": result.runner,
                "suite": result.suite,
            },
            "evidencias": [item.model_dump() for item in evidence],
            "pistas_deterministicas": [
                {"category": h.category, "confidence": h.confidence, "evidence_ids": h.evidence_ids}
                for h in diagnosis.hypotheses
            ],
        }
        try:
            raw = self.llm.complete(SYSTEM_PROMPT, build_user_prompt(context))
            llm_hypotheses = parse_llm_answer(raw, book_ids)
        except LLMError as exc:
            return diagnosis.model_copy(
                update={
                    "engine": "heuristic_fallback",
                    "llm_model": self.llm.name,
                    "warnings": [*diagnosis.warnings, f"LLM ignorado: {exc}"],
                }
            )
        llm_categories = {item.category for item in llm_hypotheses}
        merged = llm_hypotheses + [
            item for item in diagnosis.hypotheses if item.category not in llm_categories
        ]
        return diagnosis.model_copy(
            update={
                "engine": "llm",
                "llm_model": self.llm.name,
                "evidence": evidence,
                "hypotheses": sorted(merged, key=lambda item: -item.confidence),
            }
        )
