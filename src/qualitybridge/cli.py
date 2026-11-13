import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
import yaml

from qualitybridge.config import ConfigurationError, load_config
from qualitybridge.controller import run_project
from qualitybridge.flaky import analyze_runs
from qualitybridge.history import HistoryStore
from qualitybridge.llm import LLMError, client_from_env
from qualitybridge.rca import GitHistory, RootCauseAnalyzer
from qualitybridge.suggestions import suggestions_as_dicts

app = typer.Typer(
    no_args_is_help=True, help="QualityBridge — agente de testes pytest, Playwright e Jest."
)
ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="Arquivo YAML do projeto.")]


@app.command()
def init(
    runner: Annotated[str, typer.Option(help="pytest, playwright ou jest")] = "pytest",
    config: ConfigOption = Path("qualitybridge.yaml"),
    name: Annotated[str, typer.Option(help="Nome do projeto")] = "Meu projeto",
):
    """Cria uma configuração inicial sem sobrescrever arquivos."""
    if runner not in ("pytest", "playwright", "jest"):
        raise typer.BadParameter("Use pytest, playwright ou jest.")

    quick_filter = {
        "pytest": "smoke",
        "playwright": "@smoke",
        "jest": "smoke",
    }

    data = {
        "version": 1,
        "project": name,
        "runner": runner,
        "root": ".",
        "output_dir": ".qualitybridge",
        "timeout_seconds": 300,
        "suites": {
            "full": {"paths": ["tests"]},
            "quick": {"paths": ["tests"], "filter": quick_filter[runner]},
        },
    }

    try:
        with config.open("x", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, allow_unicode=True, sort_keys=False)
    except FileExistsError:
        typer.echo(f"Arquivo já existe: {config}", err=True)
        raise typer.Exit(2)
    except OSError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)

    typer.echo(f"Configuração criada: {config}. Ajuste os caminhos e marcadores dos testes.")


@app.command()
def validate(config: ConfigOption = Path("qualitybridge.yaml")):
    """Valida o YAML e os caminhos, sem executar testes."""
    try:
        settings, root, output = load_config(config)
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(f"Configuração válida: {settings.project} ({settings.runner})")
    typer.echo(f"Projeto: {root}\nResultados: {output}\nSuítes: {', '.join(settings.suites)}")


@app.command()
def run(
    config: ConfigOption = Path("qualitybridge.yaml"),
    suite: Annotated[str, typer.Option("--suite", "-s")] = "full",
    docker: Annotated[
        bool, typer.Option(help="Executa na imagem docker definida no YAML.")
    ] = False,
    timeout: Annotated[int | None, typer.Option(min=1, max=86400)] = None,
):
    """Executa testes existentes, gera relatórios e registra o histórico."""
    try:
        result, directory = run_project(config, suite, docker, timeout)
    except (ConfigurationError, OSError) as exc:
        typer.echo(f"Erro: {exc}", err=True)
        raise typer.Exit(2)
    counts = result.counts
    typer.echo(
        f"{result.project}: {result.status.upper()} | {len(result.cases)} testes | "
        f"{counts['passed']} aprovados | {counts['failed']} falhas | "
        f"{counts['error']} erros | {counts['skipped']} ignorados"
    )
    typer.echo(f"Relatório: {directory / 'report.html'}")
    for message in result.diagnostics:
        typer.echo(message, err=True)
    code = {"passed": 0, "failed": 1, "error": 2, "no_tests": 2, "timeout": 124, "cancelled": 130}[
        result.status
    ]
    raise typer.Exit(code)


@app.command()
def history(
    config: ConfigOption = Path("qualitybridge.yaml"),
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 10,
    as_json: Annotated[bool, typer.Option("--json")] = False,
):
    """Consulta o histórico de execuções do projeto."""
    try:
        settings, _, output = load_config(config)
        results = HistoryStore(output / "history.sqlite3").recent(settings.project, limit)
    except (ConfigurationError, sqlite3.Error, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    if as_json:
        typer.echo(
            json.dumps([r.model_dump(mode="json") for r in results], ensure_ascii=False, indent=2)
        )
    elif not results:
        typer.echo("Nenhuma execução registrada.")
    else:
        for result in results:
            typer.echo(
                f"{result.id} | {result.suite} | {result.status} | "
                f"{len(result.cases)} testes | {result.duration_seconds:.2f}s"
            )


@app.command()
def flaky(
    config: ConfigOption = Path("qualitybridge.yaml"),
    suite: Annotated[str, typer.Option("--suite", "-s")] = "full",
    limit: Annotated[int, typer.Option(min=2, max=1000)] = 100,
    min_runs: Annotated[int, typer.Option("--min-runs", min=2, max=1000)] = 3,
    failure_threshold: Annotated[float, typer.Option("--failure-threshold", min=0, max=1)] = 0.1,
    rerun: Annotated[
        int,
        typer.Option(
            "--rerun",
            min=0,
            max=20,
            help="Executa novamente a suíte esta quantidade de vezes.",
        ),
    ] = 0,
    docker: Annotated[bool, typer.Option(help="Usa a imagem Docker definida no YAML.")] = False,
):
    """Analisa histórico e sinaliza testes possivelmente intermitentes."""
    try:
        settings, _, output = load_config(config)
        if suite not in settings.suites:
            raise ConfigurationError(f"Suíte desconhecida: {suite}")
        if docker and settings.docker is None:
            raise ConfigurationError("Defina docker.image no YAML para usar --docker.")
        store = HistoryStore(output / "history.sqlite3")
        for attempt in range(rerun):
            result, _ = run_project(config, suite_name=suite, use_docker=docker)
            typer.echo(
                f"Reexecução {attempt + 1}/{rerun}: {result.status} ({len(result.cases)} testes)"
            )
        runs = [run for run in store.recent(settings.project, 1000) if run.suite == suite][:limit]
        metrics = analyze_runs(
            runs,
            min_runs=min_runs,
            failure_threshold=failure_threshold,
        )
    except (ConfigurationError, sqlite3.Error, OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    if not metrics:
        typer.echo("Nenhum teste encontrado no histórico para esta suíte.")
        return
    quarantine_keys = {
        (item["runner"], item["suite"], item["classname"], item["test_name"])
        for item in store.quarantined(settings.project)
    }
    output_metrics = []
    for metric in metrics:
        data = asdict(metric)
        data["quarantined"] = (
            metric.runner,
            metric.suite,
            metric.classname,
            metric.name,
        ) in quarantine_keys
        contexts = {}
        for run in runs:
            if (run.runner, run.suite) != (metric.runner, metric.suite):
                continue
            if (
                not any(
                    case.classname == metric.classname and case.name == metric.name
                    for case in run.cases
                )
                or run.context is None
            ):
                continue
            context = run.context.model_dump(mode="json")
            contexts[json.dumps(context, sort_keys=True)] = context
        data["execution_conditions"] = list(contexts.values())[:5]
        output_metrics.append(data)
    typer.echo(json.dumps(output_metrics, ensure_ascii=False, indent=2))


@app.command()
def quarantine(
    config: ConfigOption = Path("qualitybridge.yaml"),
    action: Annotated[
        str,
        typer.Option("--action", help="list, quarantine ou release."),
    ] = "list",
    test_name: Annotated[str | None, typer.Option("--test")] = None,
    classname: Annotated[str, typer.Option("--classname")] = "",
    runner: Annotated[str | None, typer.Option("--runner")] = None,
    suite: Annotated[str, typer.Option("--suite")] = "full",
    reason: Annotated[str, typer.Option("--reason")] = "",
    actor: Annotated[str, typer.Option("--actor")] = "",
    audit: Annotated[bool, typer.Option("--audit")] = False,
):
    """Lista, aplica ou remove quarentena manual com trilha de auditoria."""
    try:
        settings, _, output = load_config(config)
        store = HistoryStore(output / "history.sqlite3")
        if action == "list":
            data = (
                store.quarantine_audit(settings.project)
                if audit
                else store.quarantined(settings.project)
            )
        elif action in ("quarantine", "release"):
            if not test_name:
                raise ConfigurationError("Informe --test para alterar a quarentena.")
            selected_runner = runner or settings.runner
            if selected_runner not in ("pytest", "playwright", "jest"):
                raise ConfigurationError("Runner inválido.")
            if suite not in settings.suites:
                raise ConfigurationError(f"Suíte desconhecida: {suite}")
            store.quarantine(
                settings.project,
                selected_runner,
                suite,
                classname,
                test_name,
                action,
                reason,
                actor,
            )
            data = store.quarantine_audit(settings.project, limit=1)
        else:
            raise ConfigurationError("--action deve ser list, quarantine ou release.")
    except (ConfigurationError, sqlite3.Error, OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@app.command()
def suggestions(
    config: ConfigOption = Path("qualitybridge.yaml"),
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 100,
    suite: Annotated[str | None, typer.Option("--suite", "-s")] = None,
):
    """Apresenta hipóteses de correção baseadas em falhas e evidências do histórico."""
    try:
        settings, _, output = load_config(config)
        if suite is not None and suite not in settings.suites:
            raise ConfigurationError(f"Suíte desconhecida: {suite}")
        recent_runs = HistoryStore(output / "history.sqlite3").recent(settings.project, 1000)
        if suite:
            runs = [run for run in recent_runs if run.suite == suite][:limit]
        else:
            runs = recent_runs[:limit]
        data = suggestions_as_dicts(runs)
    except (ConfigurationError, sqlite3.Error, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@app.command()
def rca(
    config: ConfigOption = Path("qualitybridge.yaml"),
    run_id: Annotated[
        str | None, typer.Option("--run", help="ID da execução; padrão: última com falhas.")
    ] = None,
    commits: Annotated[
        int, typer.Option("--commits", min=0, max=200, help="Commits recentes analisados.")
    ] = 10,
    use_llm: Annotated[
        bool,
        typer.Option(
            "--llm/--no-llm",
            help="Consulta o LLM configurado por variáveis QUALITYBRIDGE_LLM_*.",
        ),
    ] = False,
    max_llm_cases: Annotated[int, typer.Option("--max-llm-cases", min=1, max=100)] = 10,
):
    """Investiga a causa raiz das falhas com stack trace, Git, histórico e LLM opcional."""
    try:
        settings, root, output = load_config(config)
        runs = HistoryStore(output / "history.sqlite3").recent(settings.project, 1000)
        if run_id:
            target = next((run for run in runs if run.id == run_id), None)
            if target is None:
                raise ConfigurationError(f"Execução não encontrada: {run_id}")
        else:
            target = next(
                (run for run in runs if any(c.status in ("failed", "error") for c in run.cases)),
                None,
            )
            if target is None:
                typer.echo("Nenhuma execução com falhas registrada no histórico.")
                return
        llm = client_from_env() if use_llm else None
        if use_llm and llm is None:
            raise ConfigurationError(
                "Nenhum LLM configurado. Defina ANTHROPIC_API_KEY ou "
                "QUALITYBRIDGE_LLM_PROVIDER/QUALITYBRIDGE_LLM_MODEL (veja docs/rca.md)."
            )
        changes = GitHistory(root, max_commits=commits) if commits else None
        report = RootCauseAnalyzer(
            changes=changes, llm=llm, history=runs, max_llm_cases=max_llm_cases
        ).analyze(target)
    except (ConfigurationError, LLMError, sqlite3.Error, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    data = report.model_dump_json(indent=2)
    run_directory = output / "runs" / target.id
    if run_directory.is_dir():
        (run_directory / "rca.json").write_text(data, encoding="utf-8")
    typer.echo(data)
    for diagnosis in report.diagnoses:
        if diagnosis.primary:
            typer.echo(
                f"{diagnosis.test_name}: {diagnosis.primary.category} "
                f"({diagnosis.primary.confidence:.0%}) — {diagnosis.primary.cause}",
                err=True,
            )
